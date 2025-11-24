from odoo import models, fields, api, _
from datetime import datetime, timedelta
from odoo.exceptions import UserError


class Enrollment(models.Model):
    _name = 'patient.enrollment'
    _description = 'Patient Enrollment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one('res.users', string="Doctor", required=True, readonly=True, default=lambda self: self.env.user)
    enrollment_date = fields.Date(string="Enrollment Date", required=True, default=lambda self: self._ist_date(), tracking=True)
    daily_sheet_ref = fields.Integer(string="Daily Sheet Reference", required=True, tracking=True)
    total_amount = fields.Integer(string="Total Amount", required=True, tracking=True)
    therapy_amount = fields.Integer(string="Therapy Amount", required=True, tracking=True)
    first_cons_charges = fields.Integer(string="First Consultation Charges", required=True, tracking=True)
    therapy_medicine = fields.Integer(string="Therapy + Medicine", required=True, tracking=True)
    total_sessions = fields.Integer(string="Total Sessions", required=True, tracking=True)
    remaining_sessions = fields.Integer(string="Remaining Sessions", required=True, compute='_compute_remaining_sessions')
    used_sessions = fields.Integer(string="Used Sessions", required=True, default=0, tracking=True)
    notes = fields.Char(string="Notes", tracking=True)
    enrollment_type = fields.Selection([
        ('clinic', 'Clinic'),
        ('home', 'Home'),
        ('self', 'Self'),
    ], string="Enrollment Type", required=True, tracking=True)
    state = fields.Selection([
        ('active', 'Active'),
        ('completed', 'Completed'),
    ], string="Status", default='active', tracking=True)
    pain_knee = fields.Boolean(string="Knee Pain")
    pain_spine = fields.Boolean(string="Spine Pain")
    enrolled_for = fields.Char(string="Enrolled For", compute="_compute_enrolled_for", store=True, tracking=True)
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

    @api.depends('pain_knee', 'pain_spine')
    def _compute_enrolled_for(self):
        for rec in self:
            selected = []
            if rec.pain_knee:
                selected.append("Knee Pain")
            if rec.pain_spine:
                selected.append("Spine Pain")
            rec.enrolled_for = ", ".join(selected)

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
