from odoo import models, fields, api
from datetime import datetime, timedelta
from odoo.exceptions import ValidationError
from odoo.exceptions import UserError


class PatientAssessment(models.Model):
    _name = 'patient.assessment'
    _description = 'Patient Assessment'

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user,
                                readonly=True)
    assessment_date = fields.Date(string="Date", required=True, default=lambda self: self._ist_date(),
                                  readonly=True)
    weight = fields.Float(string="Weight", digits=(3, 3), required=True)
    diagnosis = fields.Char(string="Diagnosis", required=True)
    k_c_o = fields.Char(string="K/C/O", required=True)  # Known case of
    investigation_status = fields.Char(string="Investigation Status", required=True)
    case_under_discussion = fields.Many2one("res.users", string="Case Under Discussion With")
    day_of_therapy = fields.Integer(string="Day Of Therapy", compute="_compute_day_of_therapy", store=True,
                                    readonly=True)
    type_of_therapy = fields.Selection([("detox", "Detox"),
                                        ("regeneration", "Regeneration"),
                                        ("transition", "Transition"),
                                        ("nil", "Nil")], string="Type of Therapy", required=True)
    active = fields.Boolean(default=True)

    gradation_line_ids = fields.One2many(
        "gradation.followup.line", "followup_id", string="Gradation Lines"
    )

    # 1. Define the default method
    def _default_assessment_lines(self):
        lines = []

        all_symptoms = ['salivation_when_hungry',
                        'nausea_when_hungry',
                        'nausea_before_meals',
                        'uncomfortable_after_eating',
                        'sour_smell_when_hungry',
                        'no_appetite_even_if_hungry',
                        'sour_belching',
                        'joint_pain_after_acidity',
                        'palm_sole_burning',
                        'heel_pain',
                        'bitter_taste',
                        'dizziness_when_hungry',
                        'frequent_hunger',
                        'body_feels_hot',
                        'touch_feels_hot',
                        'yellow_eyes',
                        'face_looks_reddish',
                        'pitta_headache',
                        'yellow_urine',
                        'body_tenderness',
                        'if_patient_eats_after_being_very_hungry_it_results_in_bloating_indigestion_nausea',
                        'if_vomiting_happens_food_particles_come_out_undigested',
                        'smelly_stool',
                        'sticky_stool',
                        'feels_like_belching_will_come_but_cannot_belch_out',
                        'feels_like_mucus_is_stuck_in_the_throat',
                        'very_low_hunger',
                        'bad_smell_from_mouth',
                        'urine_may_be_cloudy_heavy_smell',
                        'mucus_color_may_be_cloudy_black_green_etc',
                        'saam_kapha_will_have_saam_medha_so_smelly_sweat',
                        'knee_swelling_knee_stiffness_heaviness',
                        'morning_stiffness_kapha_vaata',
                        'very_mild_hunger_or_no_hunger',
                        'cough_is_clear_white_or_milky',
                        'heavy_joints_but_not_stiff',
                        'ra_ana_increased',
                        'shifting_pain',
                        'adhman_bloating',
                        'whole_body_pain',
                        'dry_skin',
                        'skin_becomes_dark',
                        'pain_increases_in_cold',
                        'pain_increases_after_travelling',
                        'constipation_hard_stool_difficult_to_pass',
                        'tremors_shaking_in_some_patients',
                        'numbness_or_tingling_pins_and_needles',
                        'cramps_especially_calves_or_thighs',
                        'osteophytes',
                        'varicose_veins',
                        ]

        for symptom in all_symptoms:
            lines.append((0, 0, {
                'key': symptom,
                'value': False
            }))

        return lines

    # 2. Attach the default method to your One2many field
    assessment_line_ids = fields.One2many(
        "patient.assessment.line", "followup_id", string="Assessment Lines",
        default=_default_assessment_lines  # Add this!
    )

    @api.onchange('patient_id')
    def _onchange_patient_id_autofill_previous(self):
        if not self.patient_id:
            return

        domain = [('patient_id', '=', self.patient_id.id), ('active', '=', True)]

        if self._origin and self._origin.id:
            domain.append(('id', '!=', self._origin.id))

        last_followup = self.env['patient.followup'].search(
            domain,
            order='weekly_followup_date desc',
            limit=1
        )

        last_assessment = self.env['patient.assessment'].search(
            domain,
            order='assessment_date desc',
            limit=1
        )

        # Determine latest record
        latest_record = False

        if last_followup and last_assessment:
            if last_followup.weekly_followup_date >= last_assessment.assessment_date:
                latest_record = last_followup
            else:
                latest_record = last_assessment

        elif last_followup:
            latest_record = last_followup

        elif last_assessment:
            latest_record = last_assessment

        if not latest_record:
            return

        if latest_record._name == 'patient.assessment':
            fields_to_copy = [
                'diagnosis',
                'k_c_o',
                'investigation_status',
                'case_under_discussion',
                'type_of_therapy',
            ]
        else:
            fields_to_copy = [
                'diagnosis',
                'k_c_o',
                'investigation_status',
                'type_of_therapy',
            ]

        for field in fields_to_copy:
            if field in latest_record:
                self[field] = latest_record[field]

    @api.depends('patient_id')
    def _compute_day_of_therapy(self):
        for record in self:
            if record.patient_id:
                # Fetch all enrollments of this patient
                enrollments = record.env["patient.enrollment"].sudo().search([
                    ("patient_id", "=", record.patient_id.id),
                    ("active", "=", True)
                ])

                # Total used sessions across all enrollments
                total_used = sum(enrollments.mapped("used_sessions"))

                # Day of therapy = total used + 1
                record.day_of_therapy = total_used + 1
            else:
                record.day_of_therapy = 1

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True


    def action_archive(self):
        # 1. Check if the action was triggered from our locked-down dashboard
        if self.env.context.get('block_archive'):
            raise UserError("You cannot archive records directly from the Dashboard view.")

        # 2. Otherwise, allow normal archiving behavior
        return super().action_archive()

class PatientAssessmentLine(models.Model):
    _name = 'patient.assessment.line'
    _description = 'Patient Assessment Line'

    followup_id = fields.Many2one('patient.assessment', string="Assessment")

    key = fields.Selection([
        ('salivation_when_hungry', 'When the person feels hungry, water comes in the mouth (salivation) (Saam Pitta)'),
        ('nausea_when_hungry', 'Nausea when hungry (Saam Pitta)'),
        ('nausea_before_meals', 'Nausea / queasiness before meals (Saam Pitta)'),
        ('uncomfortable_after_eating', 'Uncomfortable after eating (Saam Pitta)'),
        ('sour_smell_when_hungry', 'Bad (sour/Khatta/Ambat) smell from mouth when hungry (Saam Pitta)'),
        ('no_appetite_even_if_hungry', 'Even though hungry, does not feel like eating (Saam Pitta)'),
        ('sour_belching', 'Sour belching (Saam Pitta)'),
        ('joint_pain_after_acidity', 'Joint pains after acidity (Saam Pitta)'),
        ('palm_sole_burning', 'Palm, sole burning (Saam Pitta,Saam Rakta)'),
        ('heel_pain', 'Heel pain (Saam Pitta, Saam Rakta)'),
        ('bitter_taste', 'Bitter taste in mouth (Pitta Vridhi)'),
        ('dizziness_when_hungry', 'Dizziness when hungry (Pitta Vridhi,Saam Pitta)'),
        ('frequent_hunger', 'Wants to eat repeatedly (hunger again within ~2 hours after meals) (Pitta Vridhi)'),
        ('body_feels_hot', 'Body feels hot (Pitta Vridhi,Saam Pitta)'),
        ('touch_feels_hot', 'Touch feels hot (Pitta Vridhi, Saam Pitta)'),
        ('yellow_eyes', 'Yellow Eyes (Pitta Vridhi)'),
        ('face_looks_reddish', 'Face looks reddish (Pitta Vridhi)'),
        ('pitta_headache', 'Headache or migraine when Pitta increases (Pitta Vridhi)'),
        ('yellow_urine', 'Yellow Urine (yellowish than normal) throughout the day (Pitta Vridhi)'),
        ('body_tenderness',
         'Body tenderness (biceps, thighs painful even with moderate pressure), knee tenderness (Pitta Vridhi, Saam Pitta, Saam Rakta, Aam vaat)'),
        ('if_patient_eats_after_being_very_hungry_it_results_in_bloating_indigestion_nausea',
         'If patient eats after being very hungry, it results in bloating, indigestion, nausea (Drav Pitta)'),
        ('if_vomiting_happens_food_particles_come_out_undigested',
         'If vomiting happens, food particles come out undigested (as it is) (Drav Pitta)'),
        ('smelly_stool', 'Smelly stool (Saam Kapha, Saam Pita)'),
        ('sticky_stool', 'Sticky stool (Saam Kapha)'),
        ('feels_like_belching_will_come_but_cannot_belch_out',
         'Feels like belching will come, but cannot belch out (Dakar ane jaise feeling ati hain par dakar nahi ati) (Saam Kapha)'),
        ('feels_like_mucus_is_stuck_in_the_throat', 'Feels like mucus is stuck in the throat (Saam Kapha)'),
        ('very_low_hunger', 'Very low hunger (Saam Kapha, Pitta Kshaya, Saam vata)'),
        ('bad_smell_from_mouth', 'Bad smell from mouth (Saam Kapha, Saam Pitta)'),
        ('urine_may_be_cloudy_heavy_smell', 'Urine may be cloudy / heavy smell (Saam Kapha)'),
        ('mucus_color_may_be_cloudy_black_green_etc', 'Mucus color may be cloudy, black, green, etc. (Saam Kapha)'),
        ('saam_kapha_will_have_saam_medha_so_smelly_sweat',
         'Saam Kapha will have Saam Medha, so smelly sweat (Saam Kapha, Saam Medha)'),
        ('knee_swelling_knee_stiffness_heaviness', 'Knee swelling, knee stiffness + heaviness (Saam Kapha)'),
        ('morning_stiffness_kapha_vaata', 'Morning stiffness (Saam Vata)'),
        ('very_mild_hunger_or_no_hunger', 'Very mild hunger or no hunger (Saam Kapha)'),
        ('cough_is_clear_white_or_milky', 'Cough is clear white or milky (Kapha Vridhi)'),
        ('heavy_joints_but_not_stiff', 'Heavy joints but not stiff (Kapha Vridhi)'),
        ('ra_ana_increased', 'RA / ANA increased (Saam Vaata)'),
        ('shifting_pain', 'Shifting pain (Saam Vaata)'),
        ('adhman_bloating', 'Adhman (bloating) (Saam Vaata)'),
        ('whole_body_pain', 'Whole body pain (Saam Vaata)'),
        ('dry_skin', 'Dry skin (Saam Vaata)'),
        ('skin_becomes_dark', 'Skin becomes dark (Saam Vaata,Vaata Vridhi)'),
        ('pain_increases_in_cold', 'Pain increases in cold (Saam Vaata)'),
        ('pain_increases_after_travelling', 'Pain increases after travelling (Saam Vaata)'),
        ('constipation_hard_stool_difficult_to_pass', 'Constipation (hard stool, difficult to pass) (Vaata Vridhi)'),
        ('tremors_shaking_in_some_patients', 'Tremors (shaking) in some patients (Vaat Vridhi, Maaja Kshaya)'),
        ('numbness_or_tingling_pins_and_needles', 'Numbness / tingling (Vaat Vridhi)'),
        ('cramps_especially_calves_or_thighs', 'Cramps especially calves / thighs (Vaat Vridhi)'),
        ('osteophytes', 'Osteophytes (Vikrut vaata, Saam Medha, Saam Asthi)'),
        ('varicose_veins', 'Varicose veins (Vaat Rakta)'),

    ], string="Symptom")

    value = fields.Selection([
        ("0", "0"), ("1", "1"), ("2", "2"),
        ("3", "3"), ("4", "4"), ("5", "5")
    ], string="Score")


class Patient(models.Model):
    _inherit = 'clinic.patient'

    def action_open_assessment(self):
        """Open Assessment related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Assessment",
            "res_model": "patient.assessment",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
        }
