from odoo import api, models, _
from odoo.exceptions import ValidationError


class PatientSession(models.Model):
    _inherit = 'patient.session'

    @api.constrains('clinic_id', 'therapist_id')
    def _check_therapist_allowed_branch(self):
        for rec in self:
            if not rec.therapist_id or not rec.clinic_id:
                continue

            if rec.clinic_id not in rec.therapist_id.allowed_branch_ids:
                raise ValidationError(
                    _(
                        "%(therapist)s is not allowed "
                        "for %(clinic)s."
                    )
                    % {
                        'therapist': rec.therapist_id.name,
                        'clinic': rec.clinic_id.name,
                    }
                )