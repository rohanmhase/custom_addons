from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class PatientPSE(models.Model):
    _name = 'patient.pse'
    _description = 'Patient PSE'
    _order = 'id desc'

    patient_id = fields.Many2one(
        'clinic.patient',
        string='Patient',
        required=True,
        readonly=True,
        ondelete='cascade',
        index=True,
    )
    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Clinic',
        related='patient_id.clinic_id',
        store=True,
        readonly=True,
        index=True,
    )
    mrn = fields.Char(
        string='MRN',
        related='patient_id.mrn',
        readonly=True,
    )
    interaction_ids = fields.One2many(
        'patient.pse.interaction',
        'pse_id',
        string='Interactions',
    )
    feedback_ids = fields.One2many(
        'patient.pse.feedback',
        'pse_id',
        string='Feedback',
    )

    _sql_constraints = [
        (
            'unique_patient_pse',
            'unique(patient_id)',
            'A PSE screen already exists for this patient.',
        ),
    ]

    def action_log_interaction(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Log Interaction'),
            'res_model': 'patient.pse.interaction',
            'view_mode': 'form',
            'view_id': self.env.ref('patient_management.view_patient_pse_interaction_form').id,
            'target': 'current',
            'context': {
                'default_pse_id': self.id,
            },
        }

    def action_log_feedback(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Log Feedback'),
            'res_model': 'patient.pse.feedback',
            'view_mode': 'form',
            'view_id': self.env.ref('patient_management.view_patient_pse_feedback_form').id,
            'target': 'current',
            'context': {
                'default_pse_id': self.id,
            },
        }


class PatientPSEInteraction(models.Model):
    _name = 'patient.pse.interaction'
    _description = 'PSE Interaction Log'
    _order = 'log_date desc, id desc'

    pse_id = fields.Many2one(
        'patient.pse',
        string='PSE',
        required=True,
        readonly=True,
        ondelete='cascade',
        index=True,
    )
    patient_id = fields.Many2one(
        'clinic.patient',
        string='Patient',
        related='pse_id.patient_id',
        store=True,
        readonly=True,
        index=True,
    )
    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Clinic',
        related='patient_id.clinic_id',
        store=True,
        readonly=True,
        index=True,
    )
    log_date = fields.Date(
        string='Log Date',
        required=True,
        default=fields.Date.context_today,
        readonly=True,
        index=True,
    )
    interaction_with = fields.Selection(
        [
            ('patient', 'Patient'),
            ('companion', 'Companion'),
        ],
        string='Interaction With',
        required=True,
    )
    relation_to_patient = fields.Selection(
        [
            ('spouse', 'Spouse'),
            ('son_daughter', 'Son/Daughter'),
            ('relative', 'Relative'),
            ('friend', 'Friend'),
        ],
        string='Relation to Patient',
    )
    referral = fields.Selection(
        [
            ('yes', 'Y'),
            ('no', 'N'),
        ],
        string='Referral',
        required=True,
    )
    relief_percent = fields.Selection(
        [(str(i), str(i)) for i in range(0, 101, 10)],
        string='Relief %'
    )
    experience_rating = fields.Selection(
        [
            ('1', '1 - Very Bad'),
            ('2', '2 - Bad'),
            ('3', '3 - Okay'),
            ('4', '4 - Good'),
            ('5', '5 - Very Good'),
        ], string='Experience Rating')
    mood = fields.Selection(
        [
            ('happy', 'Happy'),
            ('neutral', 'Neutral'),
            ('anxious', 'Anxious'),
            ('frustrated', 'Frustrated'),
        ],
        string='Mood',
    )
    patient_potential = fields.Selection(
        [
            ('dropout_risk', 'Drop-out Risk'),
            ('undecided', 'Undecided'),
            ('extension_low', 'Extension - Low'),
            ('extension_high', 'Extension - High'),
        ],
        string='Patient Potential',
        required=True,
    )
    comments = fields.Text(string='Comments', required=True)
    logged_by = fields.Many2one(
        'res.users',
        string='Logged By',
        default=lambda self: self.env.user,
        readonly=True,
    )

    @api.onchange('interaction_with')
    def _onchange_interaction_with(self):
        for rec in self:
            if rec.interaction_with != 'companion':
                rec.relation_to_patient = False

    @api.constrains('interaction_with', 'relation_to_patient')
    def _check_companion_relation(self):
        for rec in self:
            if rec.interaction_with == 'companion' and not rec.relation_to_patient:
                raise ValidationError(_('Relation to Patient is mandatory for a companion interaction.'))
            if rec.interaction_with != 'companion' and rec.relation_to_patient:
                raise ValidationError(_('Relation to Patient can only be set for a companion interaction.'))

    def write(self, vals):
        today = fields.Date.context_today(self)
        for rec in self:
            # Check if the record has a log_date and is older than 2 days
            if rec.log_date and (today - rec.log_date).days > 2:
                raise ValidationError(_("You cannot edit an interaction record that is more than 2 days old."))
        return super().write(vals)

    def action_open_pse(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('PSE Screen'),
            'res_model': 'patient.pse',
            'view_mode': 'form',
            'res_id': self.pse_id.id,
            'target': 'current',
        }


class PatientPSEFeedback(models.Model):
    _name = 'patient.pse.feedback'
    _description = 'PSE Feedback Log'
    _order = 'log_date desc, id desc'

    pse_id = fields.Many2one(
        'patient.pse',
        string='PSE',
        required=True,
        readonly=True,
        ondelete='cascade',
        index=True,
    )
    patient_id = fields.Many2one(
        'clinic.patient',
        string='Patient',
        related='pse_id.patient_id',
        store=True,
        readonly=True,
        index=True,
    )
    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Clinic',
        related='patient_id.clinic_id',
        store=True,
        readonly=True,
        index=True,
    )
    log_date = fields.Date(
        string='Log Date',
        required=True,
        default=fields.Date.context_today,
        readonly=True,
        index=True,
    )
    log_type = fields.Char(
        string='Log Type',
        default='Feedback',
        readonly=True,
    )
    feedback_type = fields.Selection(
        [
            ('complaint', 'Complaint'),
            ('suggestion', 'Suggestion'),
            ('query', 'Query'),
            ('appreciation', 'Appreciation'),
            ('request', 'Request'),
        ],
        string='Feedback Type',
        required=True,
    )
    feedback_category = fields.Selection(
        [
            ('ambience', 'Ambience'),
            ('wait_time', 'Wait Time'),
            ('staff_behavior', 'Staff Behavior'),
            ('treatment_consultation', 'Treatment - Consultation'),
            ('treatment_therapy', 'Treatment - Therapy'),
            ('pricing', 'Pricing'),
            ('other', 'Other'),
        ],
        string='Feedback Category',
        required=True,
    )
    feedback_verbatim = fields.Text(
        string='Feedback (Verbatim)',
        required=True,
    )
    pse_comments = fields.Text(
        string='PSE Comments',
        required=True,
    )
    logged_by = fields.Many2one(
        'res.users',
        string='Logged By',
        default=lambda self: self.env.user,
        readonly=True,
    )

    def write(self, vals):
        today = fields.Date.context_today(self)
        for rec in self:
            # Check if the record has a log_date and is older than 2 days
            if rec.log_date and (today - rec.log_date).days > 2:
                raise ValidationError(_("You cannot edit an feedback record that is more than 2 days old."))
        return super().write(vals)

    def action_open_pse(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('PSE Screen'),
            'res_model': 'patient.pse',
            'view_mode': 'form',
            'res_id': self.pse_id.id,
            'target': 'current',
        }


class ClinicPatient(models.Model):
    _inherit = 'clinic.patient'

    def action_open_pse(self):
        self.ensure_one()

        pse = self.env['patient.pse'].search([
            ('patient_id', '=', self.id),
        ], limit=1)

        if not pse:
            pse = self.env['patient.pse'].create({
                'patient_id': self.id,
            })

        return {
            'type': 'ir.actions.act_window',
            'name': _('PSE Screen - %s') % self.name,
            'res_model': 'patient.pse',
            'view_mode': 'form',
            'view_id': self.env.ref('patient_management.view_patient_pse_form').id,
            'res_id': pse.id,
            'target': 'current',
        }
