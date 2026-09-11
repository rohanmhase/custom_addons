/** @odoo-module **/

import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { patch } from "@web/core/utils/patch";

patch(TicketScreen.prototype, {
    async _onDoRefund() {
        // 1. Get the original order before the refund starts
        const originalOrder = this.getSelectedOrder();

        // 2. Run standard Odoo refund logic (this creates the new refund order)
        await super._onDoRefund(...arguments);

        // 3. Get the newly created refund order
        const refundOrder = this.pos.get_order();

        if (originalOrder && refundOrder) {
            // Copy your custom flags directly from the old order to the new refund order
            refundOrder.included_in_package = originalOrder.included_in_package || false;
            refundOrder.enrollment_id = originalOrder.enrollment_id || false;
        }
    }
});