from odoo import models, fields

class ClinicDashboardPatientLine(models.TransientModel):
    _name = 'clinic.dashboard.patient.line'
    _description = 'Clinic Dashboard Patient Line'

    dashboard_id = fields.Many2one(
        'clinic.dashboard', ondelete='cascade'
    )

    patient_name = fields.Char(string="Patient Name")
    registration_time = fields.Datetime(string="Registration Time")
    mobile = fields.Char(string="Mobile")