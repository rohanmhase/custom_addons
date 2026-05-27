/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";

patch(ProductScreen.prototype, {

    async _setValue(val) {

        const order = this.pos.get_order();

        if (order?.enrollment_id) {

            this.popup.add(ErrorPopup, {
                title: _t("Action Restricted"),
                body: _t(
                    "Enrollment quantities cannot be modified."
                ),
            });

            return;
        }

        return super._setValue(...arguments);
    },

    async deleteOrderLine() {

        const order = this.pos.get_order();

        if (order?.enrollment_id) {

            this.popup.add(ErrorPopup, {
                title: _t("Action Restricted"),
                body: _t(
                    "Enrollment lines cannot be deleted."
                ),
            });

            return;
        }

        return super.deleteOrderLine(...arguments);
    },
});