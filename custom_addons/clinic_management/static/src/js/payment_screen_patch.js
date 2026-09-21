/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { PaymentSummaryPopup } from "./payment_summary_popup";

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {

        const order = this.currentOrder;

        // Use Odoo's native currency formatter to match the 3-decimal display perfectly
        const formatCurrency = this.env.utils.formatCurrency.bind(this.env.utils);

        // 1. Extract and format order lines
        const orderlines = order.get_orderlines().map(line => ({
            id: line.id,
            name: line.get_product().display_name,
            qty: line.get_quantity(),
            price: formatCurrency(line.get_display_price())
        }));

        // 2. Extract and format payment lines
        const paymentlines = order.get_paymentlines().map(line => ({
            id: line.cid,
            name: line.payment_method.name,
            amount: formatCurrency(line.amount)
        }));

        // 3. Extract the final payable amount (Total + Cash Rounding)
        const payableAmount = order.get_total_with_tax() + (order.get_rounding_applied() || 0);
        const totalAmountStr = formatCurrency(payableAmount);

        // 4. Trigger the custom summary popup
        const { confirmed } = await this.env.services.popup.add(PaymentSummaryPopup, {
            orderlines: orderlines,
            paymentlines: paymentlines,
            totalAmount: totalAmountStr,
        });

        if (!confirmed) {
            return false;
        }

        return super.validateOrder(...arguments);
    }
});