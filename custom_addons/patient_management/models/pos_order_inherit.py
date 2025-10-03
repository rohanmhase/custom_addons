from odoo import models, fields, api

class PosOrder(models.Model):
    _inherit = "pos.order"

    prescription_id = fields.Many2one("patient.prescription", string="Prescription")

    @api.model
    def create(self, vals):
        order = super().create(vals)

        if order.partner_id and not order.prescription_id:
            patient = self.env["clinic.patient"].search([("partner_id", "=", order.partner_id.id)], limit=1)
            prescription = self.env["patient.prescription"].search(
                [
                    ("patient_id", "=", patient.id),
                    ("state", "=", "confirmed"),
                ],
                order="id desc",
                limit=1,
            )
            if prescription:
                order.prescription_id = prescription.id

        return order

    def action_pos_order_paid(self):
        res = super().action_pos_order_paid()
        for order in self:
            if order.prescription_id:
                order.prescription_id.state = "done"
        return res
