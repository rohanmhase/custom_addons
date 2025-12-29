from odoo import models, fields

class ClinicDashboardEnrollmentLine(models.TransientModel):
    _name = 'clinic.dashboard.enrollment.line'
    _description = 'Clinic Dashboard Enrollment Line'

    dashboard_id = fields.Many2one(
        'clinic.dashboard', ondelete='cascade',
    )

    patient_name = fields.Char(string="Patient Name")
    total_sessions = fields.Integer(string="Total Sessions")
    enrolled_for = fields.Char(string="Enrolled For")