/** @odoo-module **/

import { Order } from "@point_of_sale/app/store/models";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

console.log("*** POS Payment Customer Required Module Loaded (Order Model) ***");

patch(Order.prototype, {

    pay() {
        console.log("*** Order.pay() intercepted ***");
        console.log("Current partner:", this.get_partner());

        // Check if customer is selected
        if (!this.get_partner()) {
            console.log("*** No partner selected - showing popup ***");

            this.pos.popup.add(ErrorPopup, {
                title: _t("Customer Required"),
                body: _t("Please select a customer before proceeding to payment."),
            });

            return; // Stop execution
        }

        console.log("*** Partner exists - proceeding to payment screen ***");
        // Call parent method to show payment screen
        return super.pay(...arguments);
    }
});