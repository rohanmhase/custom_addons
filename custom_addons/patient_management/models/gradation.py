from odoo import models, fields, api
from datetime import datetime, timedelta, date


class GradationOrgan(models.Model):
    _name = "gradation.organ"
    _description = "Organ for Gradation"

    name = fields.Char(string="Organ Name", required=True)
    category = fields.Selection([
        ('knee', 'Knee'),
        ('shoulder', 'Shoulder'),
        ('ankle', 'Ankle'),
        ('tibia', 'Tibia'),
        ('thigh', 'Thigh'),
        ('toes', 'Toes'),
        ('upper arm', 'Upper Arm'),
        ('forearm', 'Forearm'),
        ('wrist', 'Wrist'),
        ('fingers', 'Fingers'),
        ('cervical spine', 'Cervical Spine'),
        ('thoracic spine', 'Thoracic Spine'),
        ('lumbar spine', 'Lumbar Spine'),
    ], string="Category", default='others', required=True)
    active = fields.Boolean(default=True)


class GradationFollowupLine(models.Model):
    _name = "gradation.followup.line"
    _description = "Gradation Follow-up Line"

    followup_id = fields.Many2one("patient.assessment", string="Follow-Up Reference", ondelete="cascade")
    organ_id = fields.Many2one("gradation.organ", string="Organ", required=True)
    organ_category = fields.Selection(related="organ_id.category", string="Organ Category", readonly=True)
    summary_display = fields.Char(string="Grade Summary", compute="_compute_summary_display", store=True)
    grade_1 = [('0', '0'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4')]
    grade_2 = [('0', '0'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'), ]

    # Parameters with Gradation (0-4)

    tenderness = fields.Selection(selection=grade_1, string="Tenderness")
    stiffness = fields.Selection(selection=grade_2, string="Morning Stiffness")
    pain_grade = fields.Selection(selection=grade_2, string="Pain Grade")
    swelling = fields.Selection(selection=grade_1, string="Swelling")
    edema = fields.Selection(selection=grade_1, string="Edema")
    rom = fields.Selection(selection=grade_1, string="ROM")
    discoloration = fields.Selection(selection=grade_1, string="Discoloration")
    crepitus = fields.Selection(selection=grade_1, string="Crepitus")
    slr = fields.Selection(selection=grade_1, string="SLR")
    burning = fields.Selection(selection=grade_1, string="Burning")
    rashes = fields.Selection(selection=grade_1, string="Rashes")
    pain_relief = fields.Integer(string="% Pain Relief")

    available_organ_ids = fields.Many2many(
        'gradation.organ',
        compute='_compute_available_organs',
        string='Available Organs'
    )

    @api.depends('followup_id.gradation_line_ids.organ_id')
    def _compute_available_organs(self):
        all_organs = self.env['gradation.organ'].search([('active', '=', True)])

        for rec in self:
            # If we are inside a followup record
            if rec.followup_id:
                # Get IDs already used in other lines of the same parent
                used_ids = rec.followup_id.gradation_line_ids.filtered(
                    lambda l: l.id != rec._origin.id and l.organ_id
                ).mapped('organ_id').ids
                rec.available_organ_ids = all_organs.filtered(lambda o: o.id not in used_ids)
            else:
                # Fallback for when the line is totally orphaned/new
                rec.available_organ_ids = all_organs

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        return res

    @api.onchange('followup_id')
    def _onchange_parent(self):
        self._compute_available_organs()

    @api.depends('tenderness', 'stiffness', 'pain_grade', 'swelling', 'edema',
                 'rom', 'discoloration', 'crepitus', 'slr', 'burning', 'rashes', 'pain_relief')

    def _compute_summary_display(self):
        grade_fields = [
            ('tenderness', 'Tenderness'),
            ('stiffness', 'Stiffness'),
            ('pain_grade', 'Pain Grade'),
            ('swelling', 'Swelling'),
            ('edema', 'Edema'),
            ('rom', 'Rom'),
            ('discoloration', 'Discoloration'),
            ('crepitus', 'Crepitus'),
            ('slr', 'SLR'),
            ('burning', 'Burning'),
            ('rashes', 'Rashes'),
            ('pain_relief', 'Pain Relief'),
        ]

        for record in self:
            summary_parts = []
            for field_name, label in grade_fields:
                value = getattr(record, field_name)
                # We check if value is not False (Odoo returns False for empty selection)
                if value:
                    summary_parts.append(f"{label} - {value}")

            # Join the parts with a comma or semicolon
            record.summary_display = ", ".join(summary_parts) if summary_parts else "No grades recorded"
