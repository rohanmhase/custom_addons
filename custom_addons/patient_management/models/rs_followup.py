from odoo import models, fields, api
from datetime import datetime, timedelta

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
    case_under_discussion_with = fields.Char(string="Case Under Discussion With")
    day_of_therapy = fields.Integer(string="Day Of Therapy", compute="_compute_day_of_therapy", store=True,
                                    readonly=True)
    type_of_therapy = fields.Selection([("detox", "Detox"),
                                        ("regeneration", "Regeneration"),
                                        ("transition", "Transition"),
                                        ("nil", "Nil")], string="Type of Therapy", required=True)

    gradation_line_ids = fields.One2many(
        "gradation.followup.line", "followup_id", string="Gradation Lines"
    )

    # 1. Define the default method
    def _default_assessment_lines(self):
        lines = []

        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Saam Pitta'
        }))
        saam_pitta = ['salivation_when_hungry', 'nausea_when_hungry', 'nausea_before_meals',
            'uncomfortable_after_eating', 'sour_smell_when_hungry',
            'no_appetite_even_if_hungry', 'sour_belching', 'joint_pain_after_acidity'
                      ]

        for symptom in saam_pitta:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))

        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Pitta Vridhi'
        }))

        pitta_vridhi = [
            'bitter_taste', 'dizziness_when_hungry', 'frequent_hunger',
            'body_feels_hot', 'touch_feels_hot', 'yellow_eyes',
            'face_looks_reddish', 'pitta_headache', 'yellow_urine',
            'body_tenderness'
        ]

        for symptom in pitta_vridhi:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))

        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Drav Pitta'
        }))

        drav_pitta = [
            'if_patient_eats_after_being_very_hungry_it_results_in_bloating_indigestion_nausea',
            'if_vomiting_happens_food_particles_come_out_undigested'
        ]

        for symptom in drav_pitta:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))

        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Saam Kapha'
        }))

        saam_kapha = [
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
            'morning_stiffness_kapha_vaata'
        ]

        for symptom in saam_kapha:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))

        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Kapha Vridhi'
        }))

        kapha_vridhi = [
            'very_mild_hunger_or_no_hunger',
            'no_white_tongue',
            'no_foul_smelling_breath',
            'no_smelly_stool_stool_may_be_sticky',
            'lethargy_but_not_excessive_like_saam_kapha',
            'cough_is_clear_white_or_milky',
            'heavy_joints_but_not_stiff',
        ]

        for symptom in kapha_vridhi:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))

        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Saam Vaata'
        }))

        saam_vaata = [
            'morning_stiffness',
            'ra_ana_increased',
            'shifting_pain',
            'adhman_bloating',
            'whole_body_pain',
            'joints_are_not_warm',
            'dry_skin',
            'skin_becomes_dark',
            'pain_increases_in_cold',
            'pain_increases_after_travelling',
        ]

        for symptom in saam_vaata:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))


        lines.append((0, 0, {
            'display_type': 'line_section',
            'name': 'Vaata Vridhi'
        }))

        vaata_vridhi = [
            'very_dry_skin_rough_cracked',
            'dryness_in_mouth_throat',
            'constipation_hard_stool_difficult_to_pass',
            'gas_bloating_adhman_increases_a_lot',
            'pain_increases_a_lot_sharp_pricking_type',
            'cracking_sounds_in_joints',
            'severe_body_ache_muscle_pain',

        ]

        for symptom in vaata_vridhi:
            lines.append((0, 0, {
                'key': symptom,
                'value': '0'
            }))

        return lines

    # 2. Attach the default method to your One2many field
    assessment_line_ids = fields.One2many(
        "patient.assessment.line", "followup_id", string="Assessment Lines",
        default=_default_assessment_lines  # Add this!
    )

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

class PatientAssessmentLine(models.Model):
    _name = 'patient.assessment.line'
    _description = 'Patient Assessment Line'

    followup_id = fields.Many2one('patient.assessment', string="Assessment")

    display_type = fields.Selection([
        ('line_section', "Section"),
        ('line_note', "Note")
    ], default=False)

    name = fields.Char(string="Description")

    key = fields.Selection([
        ('salivation_when_hungry', 'Salivation when hungry'),
        ('nausea_when_hungry', 'Nausea when hungry'),
        ('nausea_before_meals', 'Nausea before meals'),
        ('uncomfortable_after_eating', 'Uncomfortable after eating'),
        ('sour_smell_when_hungry', 'Sour smell from mouth'),
        ('no_appetite_even_if_hungry', 'No appetite even when hungry'),
        ('sour_belching', 'Sour belching'),
        ('joint_pain_after_acidity', 'Joint pain after acidity'),
        ('bitter_taste', 'Bitter taste'),
        ('dizziness_when_hungry', 'Dizziness when hungry'),
        ('frequent_hunger', 'Frequent hunger'),
        ('body_feels_hot', 'Body feels hot'),
        ('touch_feels_hot', 'Touch feels hot'),
        ('yellow_eyes', 'Yellow eyes'),
        ('face_looks_reddish', 'Face looks reddish'),
        ('pitta_headache', 'Pitta headache'),
        ('yellow_urine', 'Yellow urine'),
        ('body_tenderness', 'Body tenderness'),
        ('if_patient_eats_after_being_very_hungry_it_results_in_bloating_indigestion_nausea', 'If patient eats after being very hungry, it results in bloating, indigestion, nausea'),
        ('if_vomiting_happens_food_particles_come_out_undigested', 'If vomiting happens, food particles come out undigested'),
        ('smelly_stool', 'Smelly stool'),
        ('sticky_stool', 'Sticky stool'),
        ('feels_like_belching_will_come_but_cannot_belch_out', 'Feels like belching will come but cannot belch out'),
        ('feels_like_mucus_is_stuck_in_the_throat', 'Feels like mucus is stuck in the throat'),
        ('very_low_hunger', 'Very low hunger'),
        ('bad_smell_from_mouth', 'Bad smell from mouth'),
        ('urine_may_be_cloudy_heavy_smell', 'Urine may be cloudy heavy smell'),
        ('mucus_color_may_be_cloudy_black_green_etc', 'Mucus color may be cloudy black green etc'),
        ('saam_kapha_will_have_saam_medha_so_smelly_sweat', 'Saam kapha will have saam medha so smelly sweat'),
        ('knee_swelling_knee_stiffness_heaviness', 'Knee swelling knee stiffness heaviness'),
        ('morning_stiffness_kapha_vaata', 'Morning stiffness kapha vaata'),
        ('very_mild_hunger_or_no_hunger', 'Very mild hunger or no hunger'),
        ('no_white_tongue', 'No white tongue'),
        ('no_foul_smelling_breath', 'No foul-smelling breath'),
        ('no_smelly_stool_stool_may_be_sticky', 'No smelly stool (stool may be sticky)'),
        ('lethargy_but_not_excessive_like_saam_kapha', 'Lethargy, but not excessive like Saam Kapha'),
        ('cough_is_clear_white_or_milky', 'Cough is clear white or milky'),
        ('heavy_joints_but_not_stiff', 'Heavy joints but not stiff'),
        ('morning_stiffness', 'Morning stiffness'),
        ('ra_ana_increased', 'RA / ANA increased'),
        ('shifting_pain', 'Shifting pain'),
        ('adhman_bloating', 'Adhman (bloating)'),
        ('whole_body_pain', 'Whole body pain'),
        ('joints_are_not_warm', 'Joints are not warm'),
        ('dry_skin', 'Dry skin'),
        ('skin_becomes_dark', 'Skin becomes dark'),
        ('pain_increases_in_cold', 'Pain increases in cold'),
        ('pain_increases_after_travelling', 'Pain increases after travelling'),
        ('very_dry_skin_rough_cracked', 'Very dry skin rough cracked'),
        ('dryness_in_mouth_throat', 'Dryness in mouth throat'),
        ('constipation_hard_stool_difficult_to_pass', 'Constipation hard stool difficult to pass'),
        ('gas_bloating_adhman_increases_a_lot', 'Gas bloating Adhman increases a lot'),
        ('pain_increases_a_lot_sharp_pricking_type', 'Pain increases a lot sharp pricking type'),
        ('cracking_sounds_in_joints', 'Cracking sounds in joints'),
        ('severe_body_ache_muscle_pain', 'Severe body ache muscle pain'),

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
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }