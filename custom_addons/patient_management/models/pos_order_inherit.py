from odoo import models, fields, api

class PosOrder(models.Model):
    _inherit = "pos.order"

    prescription_id = fields.Many2one("patient.prescription", string="Prescription")
    enrollment_id = fields.Many2one("patient.enrollment", string="Enrollment")
    included_in_package = fields.Boolean(string="Included in Package", default=False)

    def _order_fields(self, ui_order):
        res = super()._order_fields(ui_order)

        res["prescription_id"] = ui_order.get("prescription_id") or False
        res["enrollment_id"] = ui_order.get("enrollment_id") or False
        res['included_in_package'] = ui_order.get('included_in_package') or False

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

            if order.enrollment_id:

                vals = {
                    "payment_state": "paid",
                    "payment_date": fields.Date.today(),
                    "pos_order_id": order.id,
                }

                order.enrollment_id.write(vals)

        return res

class PosSession(models.Model):
    _inherit = 'pos.session'

    def _loader_params_pos_order(self):
        # Load the fields from the backend so the POS Ticket Screen can see them
        result = super()._loader_params_pos_order()
        result['search_params']['fields'].append('included_in_package')
        result['search_params']['fields'].append('enrollment_id')
        return result