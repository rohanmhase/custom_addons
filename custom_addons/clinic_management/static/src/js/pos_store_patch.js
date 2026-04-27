/** @odoo-module **/
import { Order } from "@point_of_sale/app/store/models";
import { patch } from "@web/core/utils/patch";

patch(Order.prototype, {
    export_for_printing() {
        const result = super.export_for_printing(...arguments);
        // Force the name-only string directly into the Order's print data
        const partner = this.get_partner();
        result.partner_name_only = partner ? partner.name : false;
        return result;
    },
});