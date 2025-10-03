/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";

patch(Order.prototype, {
    async set_partner(partner) {
        super.set_partner(partner);

        if (!partner) return;

        const posStore = this.pos;
        const order = this;

        if (order.prescription_loaded) return;

        try {
            const prescriptionLines = await this.env.services.orm.call(
                "patient.prescription",
                "get_latest_prescription",
                [partner.id]
                );

            if (!prescriptionLines.length) {
                this.env.services.notification.add(
                `ℹ️ No prescription found for ${partner.name}`,
                    { type: "info" });

                return;
            }

            let added = [];
            for (const line of prescriptionLines) {
                const product = posStore.db.get_product_by_id(line.id);
                if (product) {
                    order.add_product(product, { quantity: line.qty });
                    added.push(`${product.display_name} x${line.qty}`);
                }
                else {
                    this.env.services.notification.add(`❌ Product not loaded: ${line.name}`, { type: "danger" });
                }
            }

            if (added.length) {
            order.prescription_loaded = true;
                this.env.services.notification.add(
                    `✅ Prescription loaded for ${partner.name}: ${added.join(", ")}`,
                    { type: "success" }
                );
            }
        }

        catch (err) {
            console.error("RPC Error:", err);
            this.env.services.notification.add(
                "❌ Error auto-loading prescription",
                { type: "danger" }
            );
        }
    }
});
