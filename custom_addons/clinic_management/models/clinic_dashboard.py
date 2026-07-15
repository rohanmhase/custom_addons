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
        compute="_compute_dashboard_counts"
    )

    today_registered_patients = fields.Integer(
        string="New Registered Patients",
        compute="_compute_dashboard_counts"
    )

    total_therapies = fields.Integer(
        string="Total Therapies",
        compute="_compute_dashboard_counts"
    )

    total_followups = fields.Integer(
        string="Total RS Followups",
        compute="_compute_dashboard_counts"
    )

    total_enrollment = fields.Integer(
        string="Total Enrollment",
        compute="_compute_dashboard_counts"
    )

    total_daily_followups = fields.Integer(
        string="Total CS Followups",
        compute="_compute_dashboard_counts"
    )

    @api.constrains('from_date', 'to_date')
    def _check_date_range(self):
        for rec in self:
            if rec.from_date and rec.to_date and rec.from_date > rec.to_date:
                raise ValidationError("From Date cannot be later than To Date.")

    @api.depends('clinic_id', 'from_date', 'to_date')
    def _compute_dashboard_counts(self):
        for rec in self:
            if not (rec.clinic_id and rec.from_date and rec.to_date):
                rec.update({
                    'total_patients': 0,
                    'today_registered_patients': 0,
                    'total_therapies': 0,
                    'total_followups': 0,
                    'total_enrollment': 0,
                    'total_daily_followups': 0,
                })
                continue

            rec.total_patients = self.env['clinic.patient'].search_count(
                [('clinic_id', '=', rec.clinic_id.id), ('active', '=', True)])

            rec.today_registered_patients = self.env['clinic.patient'].search_count(
                [('clinic_id', '=', rec.clinic_id.id), ('enroll_date', '>=', rec.from_date),
                 ('enroll_date', '<=', rec.to_date), ('active', '=', True)])

            rec.total_therapies = self.env['patient.session'].search_count([
                '|', ('therapy_clinic_id', '=', rec.clinic_id.id),
                '&', ('therapy_clinic_id', '=', False), ('patient_id.clinic_id', '=', rec.clinic_id.id),
                ('session_date', '>=', rec.from_date), ('session_date', '<=', rec.to_date), ('active', '=', True)])

            f_count = self.env['patient.followup'].search_count([
                ('patient_id.clinic_id', '=', rec.clinic_id.id), ('weekly_followup_date', '>=', rec.from_date),
                ('weekly_followup_date', '<=', rec.to_date), ('active', '=', True)])
            a_count = self.env['patient.assessment'].search_count([
                ('patient_id.clinic_id', '=', rec.clinic_id.id), ('assessment_date', '>=', rec.from_date),
                ('assessment_date', '<=', rec.to_date), ('active', '=', True)])
            rec.total_followups = f_count + a_count

            rec.total_enrollment = self.env['patient.enrollment'].search_count([
                ('patient_id.clinic_id', '=', rec.clinic_id.id), ('enrollment_date', '>=', rec.from_date),
                ('enrollment_date', '<=', rec.to_date), ('active', '=', True)])

            rec.total_daily_followups = self.env['patient.daily_followup'].search_count([
                ('patient_id.clinic_id', '=', rec.clinic_id.id), ('followup_date', '>=', rec.from_date),
                ('followup_date', '<=', rec.to_date), ('active', '=', True)])

    # --------------------------------------------------
    # SMART BUTTON ACTIONS
    # --------------------------------------------------

    def action_view_therapies(self):
        self.ensure_one()
        tree_view_id = self.env.ref('patient_management.view_clinic_session_dashboard_tree').id
        return {
            'name': 'Therapy Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'patient.session',
            'view_mode': 'tree',
            'views': [(tree_view_id, 'tree')],
            'domain': [
                '|', ('therapy_clinic_id', '=', self.clinic_id.id),
                '&', ('therapy_clinic_id', '=', False), ('patient_id.clinic_id', '=', self.clinic_id.id),
                ('session_date', '>=', self.from_date), ('session_date', '<=', self.to_date), ('active', '=', True)
            ],
            'context': {'create': False, 'open': False, 'edit': False, 'delete': False, 'block_archive': True}
        }

    def action_view_new_patients(self):
        self.ensure_one()
        tree_view_id = self.env.ref('patient_management.view_clinic_patient_dashboard_tree').id
        start = fields.Datetime.to_datetime(self.from_date)
        end = fields.Datetime.to_datetime(self.to_date) + timedelta(days=1)
        return {
            'name': 'New Registered Patients',
            'type': 'ir.actions.act_window',
            'res_model': 'clinic.patient',
            'view_mode': 'tree',
            'views': [(tree_view_id, 'tree')],
            'domain': [
                ('clinic_id', '=', self.clinic_id.id),
                ('create_date', '>=', start),
                ('create_date', '<', end)
            ],
            'context': {'create': False, 'open': False, 'edit': False, 'delete': False, 'block_archive': True}
        }

    def action_view_enrollments(self):
        self.ensure_one()
        tree_view_id = self.env.ref('patient_management.view_clinic_enrollment_dashboard_tree').id
        return {
            'name': 'Enrollments',
            'type': 'ir.actions.act_window',
            'res_model': 'patient.enrollment',
            'view_mode': 'tree',
            'views': [(tree_view_id, 'tree')],
            'domain': [
                ('patient_id.clinic_id', '=', self.clinic_id.id),
                ('enrollment_date', '>=', self.from_date),
                ('enrollment_date', '<=', self.to_date)
            ],
            'context': {'create': False, 'open': False, 'edit': False, 'delete': False, 'block_archive': True}
        }

    def action_view_cs_followups(self):
        self.ensure_one()
        tree_view_id = self.env.ref('patient_management.view_clinic_daily_followup_dashboard_tree').id
        return {
            'name': 'CS Followups',
            'type': 'ir.actions.act_window',
            'res_model': 'patient.daily_followup',
            'view_mode': 'tree',
            'views': [(tree_view_id, 'tree')],
            'domain': [
                ('patient_id.clinic_id', '=', self.clinic_id.id),
                ('followup_date', '>=', self.from_date),
                ('followup_date', '<=', self.to_date)
            ],
            'context': {'create': False, 'open': False, 'edit': False, 'delete': False, 'block_archive': True}
        }

    def action_view_rs_followups(self):
        self.ensure_one()

        tree_view_id = self.env.ref('patient_management.view_clinic_assessment_dashboard_tree').id

        return {
            'name': 'RS Followups',
            'type': 'ir.actions.act_window',
            'res_model': 'patient.assessment',
            'view_mode': 'tree',
            'views': [(tree_view_id, 'tree')],
            'domain': [
                ('patient_id.clinic_id', '=', self.clinic_id.id),
                ('assessment_date', '>=', self.from_date),
                ('assessment_date', '<=', self.to_date)
            ],
            'context': {'create': False, 'open': False, 'edit': False, 'delete': False, 'block_archive': True}
        }