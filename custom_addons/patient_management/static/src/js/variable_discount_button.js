/** @odoo-module **/

import { Component } from "@odoo/owl";
import { usePos } from "@point_of_sale/app/store/pos_hook";
import { useService } from "@web/core/utils/hooks";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { NumberPopup } from "@point_of_sale/app/utils/input_popups/number_popup";

export class VariableDiscountButton extends Component {
    setup() {
        this.pos = usePos();
        this.popup = useService("popup");
    }

    get isDisabled() {
        const order = this.pos.get_order();
        return order ? !!order.enrollment_id : false;
    }

    async onClick() {
        const order = this.pos.get_order();
        if (!order) return;

        // 1. Prompt the user for a discount percentage
        const { confirmed, payload } = await this.popup.add(NumberPopup, {
            title: "Enter Discount Percentage (%)",
            startingValue: 0,
            isInputSelected: true,
        });

        if (!confirmed) return;

        const discountPct = parseFloat(payload);

        // Validate input
        if (isNaN(discountPct) || discountPct < 0 || discountPct > 100) {
            return;
        }

        // 2. Apply the entered percentage across order lines
        const lines = order.get_orderlines();

        lines.forEach(line => {
            const FREE_PRODUCTS = [
                "Male_disposable_kit",
                "Female_disposable_kit",
                "Medicin_bag"
            ];

            // Skip free items or prescribed items if needed
            if (!FREE_PRODUCTS.includes(line.product.display_name)) {
                line.set_discount(discountPct);
            }
        });
    }
}

VariableDiscountButton.template = "VariableDiscountButtonTemplate";

ProductScreen.addControlButton({
    component: VariableDiscountButton,
    position: ["after", "OrderlineCustomerNoteButton"],
});