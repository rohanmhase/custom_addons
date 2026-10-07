from odoo import models, fields
from odoo.exceptions import ValidationError, UserError

class PatientPitching(models.Model):
    _name = 'patient.pitching'
    _description = 'Patient Pitching'
    _order = 'id desc'

    patient_id = fields.Many2one(
        'clinic.patient',
        string='Patient',
        required=True,
        ondelete='cascade',
        index=True,
    )

    pitching_type = fields.Selection(
        [
            ('hard', 'Hard Pitch'),
            ('soft', 'Soft Pitch'),
        ],
        string='Pitching Type',
        required=True,
    )

    remarks = fields.Text(
        string='Remarks'
    )

    pitching_history_ids = fields.One2many(
        related='patient_id.pitching_ids',
        string='Pitching History',
        readonly=True,
    )

    def action_archive(self):
        # 1. Check if the action was triggered from our locked-down dashboard
        if self.env.context.get('block_archive'):
            raise UserError("You cannot archive records directly from the Dashboard view.")

        # 2. Otherwise, allow normal archiving behavior
        return super().action_archive()