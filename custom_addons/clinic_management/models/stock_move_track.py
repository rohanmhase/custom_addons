from odoo import models, fields, api
from markupsafe import Markup


class StockMove(models.Model):
    _inherit = 'stock.move'

    def write(self, vals):
        # Loop through the lines BEFORE saving to capture the 'old' values
        for move in self:
            if move.picking_id:
                changes = []

                # 1. Track Product Changes
                if 'product_id' in vals and vals['product_id'] != move.product_id.id:
                    # Look up the new product's name using its database ID
                    new_product = self.env['product.product'].browse(vals['product_id'])
                    old_name = move.product_id.display_name or "None"
                    new_name = new_product.display_name

                    changes.append(
                        f"Product changed:<br/>"
                        f"Old: <b>{old_name}</b> → New: <b>{new_name}</b>"
                    )

                # 2. Track Demand (Quantity) Changes
                if 'product_uom_qty' in vals and vals['product_uom_qty'] != move.product_uom_qty:
                    # Use the new product name if it was changed at the same time, otherwise use the existing one
                    current_product_name = self.env['product.product'].browse(
                        vals['product_id']).display_name if 'product_id' in vals else move.product_id.display_name

                    old_qty = move.product_uom_qty
                    new_qty = vals['product_uom_qty']

                    changes.append(
                        f"Demand updated for <b>{current_product_name}</b>:<br/>"
                        f"Old: {old_qty} → New: <b>{new_qty}</b>"
                    )

                # 3. Post to Chatter if anything was changed
                if changes:
                    # Join multiple changes together in case someone changes the product AND quantity at the exact same time
                    message_body = Markup("<b>Audit Log:</b><br/>" + "<br/><br/>".join(changes))
                    move.picking_id.message_post(body=message_body)

        # Execute the actual save operation
        return super(StockMove, self).write(vals)