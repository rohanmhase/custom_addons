from odoo import models, fields

class ClinicDashboardLine(models.TransientModel):
    _name = 'clinic.dashboard.therapy.line'
    _description = 'Clinic Dashboard Therapy Line'

    dashboard_id = fields.Many2one(
        'clinic.dashboard', ondelete='cascade'
    )

    patient_name = fields.Char(string="Patient Name")
    session_day = fields.Char(string="Session Day")
    doctor_name = fields.Char(string="Doctor Name")
