from odoo import models, fields, api
from datetime import timedelta
from odoo.exceptions import ValidationError

class ClinicDashboard(models.TransientModel):
    _name = 'clinic.dashboard'
    _description = 'Clinic Dashboard'

    clinic_id = fields.Many2one('clinic.clinic', required=True)

    from_date = fields.Date(string="From Date", default=lambda self: fields.Date.today())

    to_date = fields.Date(string="To Date", default=lambda self: fields.Date.today())

    total_patients = fields.Integer(
        string="Total Registered Patients",
        compute="_compute_total_patients"
    )

    total_therapies = fields.Integer(
        string="Total Therapies",
        compute="_compute_total_therapies"
    )

    today_registered_patients = fields.Integer(
        string="New Registered Patients",
        compute="_compute_today_registered_patients"
    )

    total_followups = fields.Integer(
        string="Total Followups",
        compute="_compute_total_followups"
    )

    total_enrollment = fields.Integer(
        string="Total Enrollment",
        compute="_compute_total_enrollment"
    )

    line_ids = fields.One2many(
        'clinic.dashboard.therapy.line',
        'dashboard_id',
        string="Therapy List"
    )

    total_daily_followups = fields.Integer(
        string="Total Daily Followups",
        compute="_compute_total_daily_followups"
    )

    patient_line_ids = fields.One2many(
        'clinic.dashboard.patient.line',
        'dashboard_id',
        string="Registered Patients"
    )

    followup_line_ids = fields.One2many(
        'clinic.dashboard.followup.line',
        'dashboard_id',
        string="Followups"
    )

    enrollment_line_ids = fields.One2many(
        'clinic.dashboard.enrollment.line',
        'dashboard_id',
        string="Enrollments"
    )

    daily_followup_line_ids = fields.One2many(
        'clinic.dashboard.daily.followup.line',
        'dashboard_id',
        string="Daily Followups"
    )

    @api.constrains('from_date', 'to_date')
    def _check_date_range(self):
        for rec in self:
            if rec.from_date and rec.to_date and rec.from_date > rec.to_date:
                raise ValidationError("From Date cannot be later than To Date.")

    @api.depends('clinic_id')
    def _compute_total_patients(self):
        for rec in self:
            rec.total_patients = self.env['clinic.patient'].search_count([
                ('clinic_id', '=', rec.clinic_id.id)
            ])

    @api.depends('clinic_id', 'from_date', 'to_date')
    def _compute_today_registered_patients(self):
        for rec in self:
            if rec.from_date and rec.to_date:
                # Convert date to datetime for comparison
                start = fields.Datetime.to_datetime(rec.from_date)
                end = fields.Datetime.to_datetime(rec.to_date) + timedelta(days=1)

                rec.today_registered_patients = self.env['clinic.patient'].search_count([
                    ('clinic_id', '=', rec.clinic_id.id),
                    ('create_date', '>=', start),
                    ('create_date', '<', end)
                ])
            else:
                rec.today_registered_patients = 0

    @api.depends('line_ids')
    def _compute_total_therapies(self):
        for rec in self:
            rec.total_therapies = len(rec.line_ids)

    @api.depends('followup_line_ids')
    def _compute_total_followups(self):
        for rec in self:
            rec.total_followups = len(rec.followup_line_ids)

    @api.depends('enrollment_line_ids')
    def _compute_total_enrollment(self):
        for rec in self:
            rec.total_enrollment = len(rec.enrollment_line_ids)

    @api.depends('daily_followup_line_ids')
    def _compute_total_daily_followups(self):
        for rec in self:
            rec.total_daily_followups = len(rec.daily_followup_line_ids)

    # --------------------------------------------------
    # ONCHANGE LOAD DASHBOARD (DATE RANGE)
    # --------------------------------------------------

    @api.onchange('clinic_id', 'from_date', 'to_date')
    def _onchange_load_dashboard(self):
        self.line_ids = [(5, 0, 0)]
        self.patient_line_ids = [(5, 0, 0)]
        self.followup_line_ids = [(5, 0, 0)]
        self.enrollment_line_ids = [(5, 0, 0)]
        self.daily_followup_line_ids = [(5, 0, 0)]

        if not self.clinic_id or not self.from_date or not self.to_date:
            return

        start_date = fields.Datetime.to_datetime(self.from_date)
        end_date = fields.Datetime.to_datetime(self.to_date) + timedelta(days=1)

        # Therapy Sessions
        sessions = self.env['patient.session'].search([
            ('patient_id.clinic_id', '=', self.clinic_id.id),
            ('session_date', '>=', self.from_date),
            ('session_date', '<=', self.to_date),
        ])

        self.line_ids = [(0, 0, {
            'patient_name': s.patient_id.name,
            'session_day': s.session_day,
            'doctor_name' : s.doctor_id.name,
        }) for s in sessions]

        # New Registered Patients

        new_patients = self.env['clinic.patient'].search([
            ('clinic_id', '=', self.clinic_id.id),
            ('create_date', '>=', start_date),
            ('create_date', '<', end_date)
        ])

        self.patient_line_ids = [(0, 0, {
            'patient_name': p.name,
            'mobile': p.phone,
        }) for p in new_patients]

        # Followups

        followups = self.env['patient.followup'].search([
            ('patient_id.clinic_id', '=', self.clinic_id.id),
            ('weekly_followup_date', '>=', self.from_date),
            ('weekly_followup_date', '<=', self.to_date),
        ])

        self.followup_line_ids = [(0, 0, {
            'patient_name': f.patient_id.name,
            'doctor_name': f.doctor_id.name,
        }) for f in followups]

        # Enrollments

        enrollments = self.env['patient.enrollment'].search([
            ('patient_id.clinic_id', '=', self.clinic_id.id),
            ('enrollment_date', '>=', self.from_date),
            ('enrollment_date', '<=', self.to_date),
        ])
        self.enrollment_line_ids = [(0, 0, {
            'patient_name': e.patient_id.name,
            'total_sessions': e.total_sessions,
            'enrolled_for': e.enrolled_for,
        }) for e in enrollments]

        # Daily Followups

        daily_followups = self.env['patient.daily_followup'].search([
            ('patient_id.clinic_id', '=', self.clinic_id.id),
            ('followup_date', '>=', self.from_date),
            ('followup_date', '<=', self.to_date),
        ])
        self.daily_followup_line_ids = [(0, 0, {
            'patient_name':d.patient_id.name,
            'doctor_name':d.doctor_id.name,
        }) for d in daily_followups]
