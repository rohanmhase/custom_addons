/** @odoo-module **/

import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { useService } from "@web/core/utils/hooks";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";

class BasePackageButton extends Component {
    setup() {
        this.pos = usePos();
        this.popup = useService("popup");
    }

    get isDisabled() {
        const order = this.pos.get_order();
        if (!order || order.get_orderlines().length === 0) {
            return true;
        }

        // Disable the button if it's an enrollment OR a refund
        const isRefund = order.get_orderlines().some(line => line.refunded_orderline_id || line.get_quantity() < 0);
        return !!order.enrollment_id || isRefund;
    }

    checkRestrictions(order) {
        if (order.get_orderlines().length === 0) {
            this.popup.add(ErrorPopup, {
                title: "Empty Cart",
                body: "Please add at least one product before selecting a package option.",
            });
            return false;
        }

        const isRefund = order.get_orderlines().some(line => line.refunded_orderline_id);
        if (order.enrollment_id || isRefund) {
            this.popup.add(ErrorPopup, {
                title: "Action Restricted",
                body: "Package selection cannot be used on enrollment orders or refund orders.",
            });
            return false;
        }
        return true;
    }
}

// 1. Included Button
export class IncludedButton extends BasePackageButton {
    get isActive() {
        const order = this.pos.get_order();
        return order ? order.included_in_package === true : false;
    }
    onClick() {
        const order = this.pos.get_order();
        if (!order || !this.checkRestrictions(order)) return;
        order.set_package_status(true);
    }
}
IncludedButton.template = "IncludedButtonTemplate";

// 2. Not Included Button
export class NotIncludedButton extends BasePackageButton {
    get isActive() {
        const order = this.pos.get_order();
        return order ? order.included_in_package === false : false;
    }
    onClick() {
        const order = this.pos.get_order();
        if (!order || !this.checkRestrictions(order)) return;
        order.set_package_status(false);
    }
}
NotIncludedButton.template = "NotIncludedButtonTemplate";

// Register both buttons
ProductScreen.addControlButton({
    component: IncludedButton,
    position: ["after", "OrderlineCustomerNoteButton"],
});
ProductScreen.addControlButton({
    component: NotIncludedButton,
    position: ["after", "OrderlineCustomerNoteButton"],
});