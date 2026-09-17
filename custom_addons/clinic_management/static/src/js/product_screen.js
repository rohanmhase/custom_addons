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

        const isRefund = this.get_orderlines().some(line => line.refunded_orderline_id || line.get_quantity() < 0);

        if (!isRefund && this.get_orderlines().length > 0 && this.included_in_package === null) {
            this.pos.popup.add(ErrorPopup, {
                title: _t("Selection Required"),
                body: _t("Please select 'Included in Package' or 'Not Included' before proceeding to payment."),
            });
            return;
        }

        console.log("*** Partner exists - proceeding to payment screen ***");
        // Call parent method to show payment screen
        return super.pay(...arguments);
    }
});