from odoo import models, fields, api

class PosOrder(models.Model):
    _inherit = "pos.order"

    prescription_id = fields.Many2one("patient.prescription", string="Prescription")

    def _order_fields(self, ui_order):
        res = super()._order_fields(ui_order)

        res["prescription_id"] = ui_order.get("prescription_id") or False

        return res

    def action_pos_order_paid(self):
        res = super().action_pos_order_paid()

        for order in self:
            if order.prescription_id:

                prescribed = {
                    line.product_id.id: line.qty
                    for line in order.prescription_id.line_ids
                }

                sold = {}
                for line in order.lines:
                    sold[line.product_id.id] = sold.get(line.product_id.id, 0) + line.qty

                matched = 0
                missing_products = []

                for line in order.prescription_id.line_ids:
                    product_id = line.product_id.id
                    if sold.get(product_id, 0) >= line.qty:
                        matched += 1
                    else:
                        missing_products.append(line.product_id.display_name)

                if matched == 0:
                    order.prescription_id.state = "confirmed"

                elif matched < len(prescribed):
                    order.prescription_id.state = "partial"

                    order.prescription_id._send_partial_notification_mail(missing_products)

                else:
                    order.prescription_id.state = "done"

        return res