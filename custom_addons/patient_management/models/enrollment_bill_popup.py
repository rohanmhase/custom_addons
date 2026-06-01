from odoo import models, fields, api


class EnrollmentBillPopup(models.TransientModel):
    _name = 'enrollment.bill.popup'
    _description = 'Enrollment Bill Popup'

    enrollment_id = fields.Many2one(
        'patient.enrollment',
        string="Enrollment"
    )

    total_amount = fields.Float(
        string="Total Amount",
        readonly=True
    )

    line_ids = fields.Many2many(
        'patient.enrollment.line',
        string="Services",
        readonly=True
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        enrollment_id = self.env.context.get('default_enrollment_id')

        if enrollment_id:
            enrollment = self.env['patient.enrollment'].browse(enrollment_id)

            res.update({
                'line_ids': [(6, 0, enrollment.line_ids.ids)],
                'total_amount': enrollment.total_amount,
            })

        return res

    def action_yes(self):
        self.ensure_one()

        enrollment = self.enrollment_id

        enrollment.payment_state = 'bill_created'

        return {'type': 'ir.actions.act_window_close'}