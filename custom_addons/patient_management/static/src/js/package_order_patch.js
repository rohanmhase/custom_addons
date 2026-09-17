/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";

const FREE_PRODUCTS = [
    "Male_disposable_kit",
    "Female_disposable_kit",
    "Medicin_bag",
];

patch(Order.prototype, {
    setup() {
        super.setup(...arguments);
        // Default to null so neither button is active[cite: 5]
        this.included_in_package =
            this.included_in_package !== undefined
                ? this.included_in_package
                : null;
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.included_in_package = this.included_in_package;
        return json;
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.included_in_package = json.included_in_package !== undefined ? json.included_in_package : null;
    },

    // Replaces toggle_package_discount to explicitly set states[cite: 5]
    set_package_status(isIncluded) {
        this.included_in_package = isIncluded;
        const lines = this.get_orderlines();

        lines.forEach(line => {
            const isFreeProduct = FREE_PRODUCTS.includes(line.product.display_name);
            if (isFreeProduct) return;
            line.set_discount(this.included_in_package ? 100 : 0);
        });

        return this.included_in_package;
    },

    add_product(product, options = {}) {
        const result = super.add_product(...arguments);

        if (options.refunded_orderline_id) {
            return result;
        }

        // Only auto-apply if explicitly set to true[cite: 5]
        if (this.included_in_package === true) {
            const isFreeProduct = FREE_PRODUCTS.includes(product.display_name);
            if (!isFreeProduct) {
                const line = this.get_selected_orderline();
                if (line) {
                    line.set_discount(100);
                }
            }
        }
        return result;
    }
});