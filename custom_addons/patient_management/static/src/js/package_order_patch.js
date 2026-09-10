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
        this.included_in_package =
            this.included_in_package !== undefined
                ? this.included_in_package
                : true; // default ON for new orders
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.included_in_package = this.included_in_package || false;
        return json;
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.included_in_package = json.included_in_package || false;
    },

    // Toggle the package flag and immediately apply/remove
    // 100% discount on every existing line (except free combos).
    toggle_package_discount() {
        // The check for this.prescription_id has been removed here
        // to allow the user to toggle the discount off.

        this.included_in_package = !this.included_in_package;

        const lines = this.get_orderlines();

        lines.forEach(line => {
            const isFreeProduct = FREE_PRODUCTS.includes(
                line.product.display_name
            );

            if (isFreeProduct) {
                return;
            }

            line.set_discount(
                this.included_in_package ? 100 : 0
            );
        });

        return this.included_in_package;
    },

    add_product(product, options = {}) {
        const result = super.add_product(...arguments);

        // Auto-apply discount to any product added while the
        // package flag is active.
        if (this.included_in_package) {
            const isFreeProduct = FREE_PRODUCTS.includes(
                product.display_name
            );

            if (!isFreeProduct) {
                const line = this.get_selected_orderline();
                if (line) {
                    line.set_discount(100);
                }
            }
        }

        return result;
    },

    activate_package_discount() {
        // Only trigger if it is currently inactive
        if (!this.included_in_package) {
            this.included_in_package = true;

            const lines = this.get_orderlines();

            lines.forEach(line => {
                const isFreeProduct = FREE_PRODUCTS.includes(
                    line.product.display_name
                );

                if (!isFreeProduct) {
                    line.set_discount(100);
                }
            });
        }
    },

    deactivate_package_discount() {
        // Only trigger if it is currently active
        if (this.included_in_package) {
            this.included_in_package = false;

            const lines = this.get_orderlines();

            lines.forEach(line => {
                const isFreeProduct = FREE_PRODUCTS.includes(
                    line.product.display_name
                );

                if (!isFreeProduct) {
                    line.set_discount(0); // Restore price to normal
                }
            });
        }
    },
});