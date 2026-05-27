/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";

patch(Order.prototype, {

    add_product(product, options = {}) {

        // Allow only enrollment-loaded products
        if (
            this.enrollment_id &&
            !options.from_enrollment
        ) {

            this.pos.env.services.popup.add(
                ErrorPopup,
                {
                    title: _t("Action Restricted"),
                    body: _t(
                        "You cannot add extra products in enrollment orders."
                    ),
                }
            );

            return;
        }

        return super.add_product(...arguments);
    },

    removeOrderline(line) {

        if (this.enrollment_id) {

            this.pos.env.services.popup.add(
                ErrorPopup,
                {
                    title: _t("Action Restricted"),
                    body: _t(
                        "You cannot delete enrollment lines."
                    ),
                }
            );

            return;
        }

        return super.removeOrderline(...arguments);
    },
});