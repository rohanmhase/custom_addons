/** @odoo-module **/

import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { useService } from "@web/core/utils/hooks";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

export class PackageDiscountButton extends Component {

    setup() {
        this.pos = usePos();
        this.popup = useService("popup");
    }

    get isActive() {
        const order = this.pos.get_order();
        return order ? !!order.included_in_package : false;
    }

    get isDisabled() {
        const order = this.pos.get_order();
        return order ? !!order.enrollment_id : false;

        // 2. Detect if any item in the cart is a refund line
       const isRefund = order.get_orderlines().some(line => line.refunded_orderline_id || line.get_quantity() < 0);

        // 3. Disable the button if it's an enrollment OR a refund
        return !!order.enrollment_id || isRefund;
    }

    onClick() {
        const order = this.pos.get_order();

        if (!order) {
            return;
        }

        const isRefund = order.get_orderlines().some(line => line.refunded_orderline_id);

        if (order.enrollment_id || isRefund) {
            this.popup.add(ErrorPopup, {
                title: "Action Restricted",
                body: "Package discount cannot be used on enrollment orders or refund orders.",
            });
            return;
        }

        order.toggle_package_discount();
    }
}

PackageDiscountButton.template = "PackageDiscountButtonTemplate";

ProductScreen.addControlButton({
    component: PackageDiscountButton,
    position: ["after", "OrderlineCustomerNoteButton"],
});