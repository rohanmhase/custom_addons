/** @odoo-module **/

import { Order } from "@point_of_sale/app/store/models";
import { patch } from "@web/core/utils/patch";

patch(Order.prototype, {
    setup() {
        super.setup(...arguments);

        this.prescription_id = this.prescription_id || false;
        this.enrollment_id = this.enrollment_id || false;
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.prescription_id = this.prescription_id || false;
        json.enrollment_id = this.enrollment_id || false;
        return json;
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);

        this.prescription_id = json.prescription_id || false;
        this.enrollment_id = json.enrollment_id || false;
    },
});