/** @odoo-module **/

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { EnrollmentPopup } from "./enrollment_popup";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

export class EnrollmentButton extends Component {

    setup() {
        this.popup = useService("popup");
        this.pos = usePos();
        this.orm = useService("orm");
    }

    async onClick() {

        const result = await this.popup.add(EnrollmentPopup, {});

        console.log(result);

        if (result && result.record) {

            const order = this.pos.get_order();
            const rec = result.record;

            // Prevent multiple enrollments
            if (order.enrollment_id && order.enrollment_id !== rec.id) {
                this.popup.add(ErrorPopup, {
                    title: "Action Restricted",
                    body: "You cannot load multiple enrollments in the same order.",
                });

                return;
            }

            let partner = this.pos.db.get_partner_by_id(rec.patient_id);

            if (!partner) {

                const partners = await this.orm.searchRead(
                    "res.partner",
                    [["id", "=", rec.patient_id]],
                    ["name", "phone", "mobile"]
                );

                if (partners.length) {

                    this.pos.db.add_partners(partners);

                    partner = this.pos.db.get_partner_by_id(
                        rec.patient_id
                    );
                }
            }

            if (partner) {

                order.set_partner(partner);

            } else {

                this.popup.add(ErrorPopup, {
                    title: "Patient Not Found",
                    body: "Unable to load patient in POS.",
                });

                return;
            }

            // Set enrollment
            order.enrollment_id = rec.id;

            // Add enrollment products/services
            for (const line of rec.lines) {

                const product = this.pos.db.get_product_by_id(line.product_id);

                if (product) {

                    order.add_product(product, {
                        quantity: Number(line.qty || 1),
                        price: Number(line.unit_price || 0),
                        from_enrollment: true,
                    });

                    const addedLine = order.get_selected_orderline();

                    if (addedLine) {
                        addedLine.from_enrollment = true;
                    }
                }
            }
        }
    }
}

EnrollmentButton.template = "EnrollmentButtonTemplate";

ProductScreen.addControlButton({
    component: EnrollmentButton,
    position: ["after", "PrescriptionButton"],
});