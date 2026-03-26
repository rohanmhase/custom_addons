/** @odoo-module */

import { patch } from "@web/core/utils/patch";
// 📢 Notice we added Orderline to the import here!
import { Order, Orderline } from "@point_of_sale/app/store/models";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";

// 🌟 THE DOM INJECTOR
function stampProductTypes(pos) {
    const articles = document.querySelectorAll('article.product');
    articles.forEach(article => {
        const productId = parseInt(article.dataset.productId);
        if (productId) {
            const product = pos.db.get_product_by_id(productId);
            if (product && product.type) {
                article.setAttribute('data-product-type', product.type);
            }
        }
    });
}

// 🌟 THE SCREEN UPDATER
function updateScreenState(order, pos) {
    stampProductTypes(pos);

    if (!order) {
        document.body.removeAttribute('data-cart-type');
        return;
    }
    const lines = order.get_orderlines();
    if (lines.length > 0) {
        const firstType = lines[0].get_product().type;
        document.body.setAttribute('data-cart-type', firstType);
    } else {
        // This clears the screen when the last item is removed
        document.body.removeAttribute('data-cart-type');
    }
}

// 1️⃣ PATCH THE ORDER (Catches adding products)
patch(Order.prototype, {
    async add_product(product, options) {
        const lines = this.get_orderlines();

        if (lines.length > 0) {
            const firstProduct = lines[0].get_product();
            if (firstProduct.type !== product.type) {
                this.pos.env.services.popup.add(ErrorPopup, {
                    title: _t("Category Mismatch"),
                    body: _t(`You cannot mix ${firstProduct.type} and ${product.type} in one order.`),
                });
                return false;
            }
        }

        const result = await super.add_product(...arguments);
        updateScreenState(this, this.pos);
        return result;
    },

    remove_orderline(line) {
        const result = super.remove_orderline(...arguments);
        updateScreenState(this, this.pos);
        return result;
    }
});

// 2️⃣ PATCH THE ORDERLINE (Catches the Numpad Backspace/Delete)
patch(Orderline.prototype, {
    set_quantity(quantity, keep_price) {
        const result = super.set_quantity(...arguments);
        // Every time a quantity changes (including dropping to 0), update screen
        if (this.order && this.order.pos) {
            updateScreenState(this.order, this.order.pos);
        }
        return result;
    }
});

// 3️⃣ THE UI FAILSAFE (Catches payments, switching screens, and category clicks)
document.addEventListener('click', () => {
    setTimeout(() => {
        // If there are literally no orderlines shown in the HTML cart UI...
        if (!document.querySelector('.orderline')) {
            // ...force the gray-out effect to disappear instantly
            document.body.removeAttribute('data-cart-type');
        }

        // This also ensures categories are re-stamped when clicking around
        const pos = window.posmodel;
        if(pos && pos.get_order()) {
             stampProductTypes(pos);
        }
    }, 50); // Wait 50ms for Odoo to draw the UI before checking
});