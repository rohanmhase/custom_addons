/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Orderline } from "@point_of_sale/app/store/models";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";


patch(Orderline.prototype, {

    setup() {
        super.setup(...arguments);

        // ✅ Store flag from options (important)
        this.from_prescription = this.from_prescription || false;
    },

    set_quantity(quantity, keep_price) {

        // ✅ Check if this order came from prescription
        if (this.order?.prescription_id) {

            // ❌ Block ONLY increase
            if (quantity > this.quantity) {
                this.pos.env.services.popup.add(ErrorPopup, {
                    title: _t("Action Restricted"),
                    body: _t("You cannot increase quantity of prescribed medicines."),
                });
                return;
            }

            // ✅ Allow decrease
        }

        return super.set_quantity(...arguments);
    },


    set_unit_price(price) {

    const FREE_PRODUCTS = [
        "Male_disposable_kit",
        "Female_disposable_kit",
        "Medicin_bag"
    ];

    const isFreeProduct = FREE_PRODUCTS.includes(this.product.display_name);

    // ✅ Block price edit for:
    // 1. Prescription items
    // 2. Free products (even if added manually)
    if (this.order?.prescription_id || isFreeProduct) {
        return; // 🔥 silent block (no popup)
    }

    return super.set_unit_price(...arguments);
 }
});
