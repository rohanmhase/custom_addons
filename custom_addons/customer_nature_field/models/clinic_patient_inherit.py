from odoo import models, api


class ClinicPatientNatureInherit(models.Model):
    """
    Lightweight inherit — only loaded if patient_management is installed.
    Injects a context flag so res.partner.create() auto-fills nature.
    Does NOT modify any field or logic in clinic.patient.
    """
    _inherit = 'clinic.patient'

    @api.model
    def create(self, vals):
        return super(
            ClinicPatientNatureInherit,
            self.with_context(from_patient_creation=True),
        ).create(vals)