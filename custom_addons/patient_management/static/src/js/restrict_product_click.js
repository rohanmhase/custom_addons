/** @odoo-module */

import { Order } from "@point_of_sale/app/store/models";
import { patch } from "@web/core/utils/patch";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";

patch(Order.prototype, {

    set_partner(partner) {

        const oldPartner = this.get_partner();

        super.set_partner(partner);

        // CUSTOMER CHANGED
        if (
            oldPartner &&
            (
                !partner ||
                oldPartner.id !== partner.id
            )
            ) {

            // CLEAR PRESCRIPTION
            this.prescription_id = false;

            // CLEAR ENROLLMENT
            this.enrollment_id = false;

            /// REMOVE ALL ORDERLINES
            const lines = [...this.get_orderlines()];

            lines.forEach(line => {
                this.removeOrderline(line);
            });
        }
    },

    async add_product(product, options) {

        // ✅ STEP 1: Allow prescription products
        if (options?.from_prescription) {
            return super.add_product(...arguments);
        }

        if (options?.quantity && options.quantity < 0) {
            return super.add_product(...arguments);
        }

        if (options?.from_enrollment) {
            return super.add_product(...arguments);
        }

        // ✅ STEP 2: Your allowed combo products
        const allowedProducts = [
            'Male_disposable_kit',
            'Female_disposable_kit',
            'Medicin_bag'
        ];

        // ❌ STEP 3: Block everything else
        if (!allowedProducts.includes(product.display_name)) {
            this.env.services.popup.add(ErrorPopup, {
                title: _t("Action Restricted"),
                body: _t("Only allowed combo products or prescription items can be added."),
            });
            return false;
        }

        // ✅ STEP 4: Allow valid products
        return super.add_product(...arguments);
    }
});