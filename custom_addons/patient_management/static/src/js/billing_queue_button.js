/** @odoo-module **/
console.log("Billing Queue Button")
import { Component } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

import { BillingQueuePopup } from "./billing_queue_popup";

import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

export class BillingQueueButton extends Component {

    setup() {

        this.popup = useService("popup");

        this.pos = usePos();

        this.orm = useService("orm");
    }

    async onClick() {

        const result = await this.popup.add(
            BillingQueuePopup,
            {}
        );

        if (!(result && result.record)) {
            return;
        }

        const order = this.pos.get_order();

        const rec = result.record;

        // BLOCK SAME RECORD RELOAD
        if (
            (rec.queue_type === 'prescription'
                && order.prescription_id === rec.id)
            ||
            (rec.queue_type === 'enrollment'
                && order.enrollment_id === rec.id)
        ) {

            this.popup.add(ErrorPopup, {
                title: "Already Loaded",
                body: "This billing record is already loaded in the current order.",
            });

            return;
        }

        // BLOCK MULTIPLE RECORDS
        if (
            (
                rec.queue_type === 'prescription'
                && order.prescription_id
                && order.prescription_id !== rec.id
            )
            ||
            (
                rec.queue_type === 'enrollment'
                && order.enrollment_id
                && order.enrollment_id !== rec.id
            )
        ) {

            this.popup.add(ErrorPopup, {
                title: "Action Restricted",
                body:
                    "You cannot load multiple billing records in the same order.",
            });

            return;
        }

        // LOAD PARTNER
        let partner =
            this.pos.db.get_partner_by_id(
                rec.patient_id
            );

        if (!partner) {

            const partners =
                await this.orm.searchRead(
                    "res.partner",
                    [["id", "=", rec.patient_id]],
                    ["name", "phone", "mobile"]
                );

            if (partners.length) {

                this.pos.db.add_partners(
                    partners
                );

                partner =
                    this.pos.db.get_partner_by_id(
                        rec.patient_id
                    );
            }
        }

        if (!partner) {

            this.popup.add(ErrorPopup, {
                title: "Patient Not Found",
                body:
                    "Unable to load patient in POS.",
            });

            return;
        }

        order.set_partner(partner);

        // PRESCRIPTION
        if (
            rec.queue_type === 'prescription'
        ) {

            order.prescription_id = rec.id;

            for (const line of rec.lines) {

                const product =
                    this.pos.db.get_product_by_id(
                        line.product_id
                    );

                if (!product) {
                    continue;
                }

                const isFreeProduct = [
                    "Male_disposable_kit",
                    "Female_disposable_kit",
                    "Medicin_bag",
                ].includes(
                    product.display_name
                );

                let options = {
                    quantity: line.qty,
                    from_prescription: true,
                };

                if (isFreeProduct) {
                    options.price = 0;
                }

                order.add_product(
                    product,
                    options
                );
            }
        }

        // ENROLLMENT
        if (
            rec.queue_type === 'enrollment'
        ) {

            order.enrollment_id = rec.id;

            for (const line of rec.lines) {

                const product =
                    this.pos.db.get_product_by_id(
                        line.product_id
                    );

                if (!product) {
                    continue;
                }

                order.add_product(product, {
                    quantity:
                        Number(line.qty || 1),

                    price:
                        Number(
                            line.unit_price || 0
                        ),

                    from_enrollment: true,
                });

                const addedLine =
                    order.get_selected_orderline();

                if (addedLine) {

                    addedLine.from_enrollment = true;
                }
            }
        }
    }
}

BillingQueueButton.template =
    "BillingQueueButtonTemplate";

ProductScreen.addControlButton({
    component: BillingQueueButton,
    position: [
        "after",
        "OrderlineCustomerNoteButton"
    ],
});