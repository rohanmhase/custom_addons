from odoo import models, fields, api, _
from datetime import datetime, timedelta
from odoo.exceptions import UserError


class Enrollment(models.Model):
    _name = 'patient.enrollment'
    _description = 'Patient Enrollment'

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one('res.users', string="Doctor", required=True, readonly=True, default=lambda self: self.env.user)
    enrollment_date = fields.Date(string="Enrollment Date", required=True, readonly=True, default=lambda self: self._ist_date())
    daily_sheet_ref = fields.Integer(string="Daily Sheet Reference", required=True)
    total_amount = fields.Integer(string="Total Amount", required=True)
    therapy_amount = fields.Integer(string="Therapy Amount", required=True)
    first_cons_charges = fields.Integer(string="First Consultation Charges", required=True)
    therapy_medicine = fields.Integer(string="Therapy + Medicine", required=True)
    total_sessions = fields.Integer(string="Total Sessions", required=True)
    remaining_sessions = fields.Integer(string="Remaining Sessions", required=True, compute='_compute_remaining_sessions')
    used_sessions = fields.Integer(string="Used Sessions", readonly=True, required=True, default=0)
    notes = fields.Char(string="Notes")
    enrollment_type = fields.Selection([
        ('clinic', 'Clinic'),
        ('home', 'Home'),
        ('self', 'Self'),
    ], string="Enrollment Type", required=True)
    state = fields.Selection([
        ('active', 'Active'),
        ('completed', 'Completed'),
    ], string="Status", default='active')
    active = fields.Boolean(default=True)


    @api.depends('total_sessions', 'used_sessions')
    def _compute_remaining_sessions(self):
        for rec in self:
            new_remaining = rec.total_sessions - rec.used_sessions
            # Only update if value actually changed
            if rec.remaining_sessions != new_remaining:
                rec.remaining_sessions = new_remaining

            # Update state only if needed
            if new_remaining == 0 and rec.state != 'completed':
                rec.state = 'completed'
            elif new_remaining > 0 and rec.state != 'active':
                rec.state = 'active'

    def write(self, vals):
        for rec in self:
            if rec.state == 'completed':
                raise UserError(_('You cannot modify an enrollment that is already completed'))
            return super(Enrollment, self).write(vals)

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True
