/** @odoo-module **/

import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PrescriptionPopup } from "./prescription_popup";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

export class PrescriptionButton extends Component {
    setup() {
        this.popup = useService("popup");
        this.pos = usePos();
        this.orm = useService("orm");
    }

    async onClick() {
        console.log("Prescription button clicked");

        const result = await this.popup.add(PrescriptionPopup, {});

        console.log(result)

        if (result && result.record) {
            const order = this.pos.get_order();
            const rec = result.record;

            if (order.prescription_id && order.prescription_id !== rec.id) {
            this.popup.add(ErrorPopup, {
                title: "Action Restricted",
                body: "You cannot load multiple prescriptions in the same order.",
            });
            return;
            }

            let partner = this.pos.db.get_partner_by_id(rec.patient_id);

            if (!partner) {
                const partners = await this.orm.searchRead(
                    'res.partner',
                    [['id', '=', rec.patient_id]], ['name', 'phone', 'mobile'],
                );

                if (partners.length) {
                    this.pos.db.add_partners(partners);
                    partner = this.pos.db.get_partner_by_id(rec.patient_id);
                }
            }

            if (partner) {
                order.set_partner(partner);
            } else {
                this.popup.add(ErrorPopup, {
                    title: "Patient Not Found",
                    body: "Unable to load patient in POS.",
                });
            }

            order.prescription_id = rec.id;

            for (const line of rec.lines) {
                const product = this.pos.db.get_product_by_id(line.product_id);

                if (product) {

                    const isFreeProduct = [
                        "Male_disposable_kit",
                        "Female_disposable_kit",
                        "Medicin_bag"
                    ].includes(product.display_name);

                let options = {
                    quantity: line.qty,
                    from_prescription: true,
                };

                    if (isFreeProduct) {
                        options.price = 0;
                    }

                    order.add_product(product, options);
                }
            }
        }
    }
}

PrescriptionButton.template = "PrescriptionButtonTemplate";

ProductScreen.addControlButton({
    component: PrescriptionButton,
    position: ["after", "OrderlineCustomerNoteButton"],
});