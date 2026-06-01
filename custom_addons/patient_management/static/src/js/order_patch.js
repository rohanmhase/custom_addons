/** @odoo-module **/

import { Order } from "@point_of_sale/app/store/models";
import { patch } from "@web/core/utils/patch";

patch(Order.prototype, {
    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.prescription_id = this.prescription_id || false;
        json.enrollment_id = this.enrollment_id || false;
        return json;
    },
});