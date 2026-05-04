from odoo import models, fields

class ClinicDashboardFollowupLine(models.TransientModel):
    _name = 'clinic.dashboard.followup.line'
    _description = 'Clinic Dashboard Followup Line'

    dashboard_id = fields.Many2one(
        'clinic.dashboard', ondelete='cascade'
    )

    patient_name = fields.Char(string="Patient Name")
    doctor_name = fields.Char(string="Doctor Name")


class ClinicDashboardAssessmentLine(models.TransientModel):
    _name = 'clinic.dashboard.assessment.line'
    _description = 'Clinic Dashboard Assessment Line'

    dashboard_id = fields.Many2one(
        'clinic.dashboard', ondelete='cascade'
    )

    patient_name = fields.Char(string="Patient Name")
    doctor_name = fields.Char(string="Doctor Name")