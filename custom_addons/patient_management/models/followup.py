from odoo import models, fields, api
from datetime import datetime, timedelta


class Followup(models.Model):
    _name = "patient.followup"
    _description = "Followup Details"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user, readonly=True)
    weekly_followup_date = fields.Date(string="Follow Up Date", required=True, default=lambda self: self._ist_date(), readonly=True)
    weight = fields.Float(string="Weight", digits=(3, 3), required=True)
    diagnosis = fields.Char(string="Diagnosis", required=True)
    k_c_o = fields.Char(string="K/C/O", required=True) # Known case of
    investigation_status = fields.Char(string="Investigation Status", required=True)
    case_under_discussion_with = fields.Char(string="Case Under Discussion With")
    day_of_therapy = fields.Integer(string="Day Of Therapy", compute="_compute_day_of_therapy", store=True, readonly=True)
    type_of_therapy = fields.Selection([("detox", "Detox"),
                                        ("regeneration", "Regeneration"),
                                        ("transition", "Transition"),
                                        ("nil", "Nil")], string="Type of Therapy", required=True)

    # Shakhagata Examinations

    # Left Leg
    morning_stiffness_with_duration_lt = fields.Char(string="Morning stiffness with duration left:", required=True)
    b_l_shin_tenderness_with_gradation_lt = fields.Char(string="B/L shin tenderness with gradation left:", required=True)
    b_l_knee_tenderness_with_gradation_and_position_lt = fields.Char(
        string="B/L Knee tenderness with gradation and position left:", required=True)
    b_l_shin_oedema_with_gradation_lt = fields.Char(string="B/L shin oedema with gradation left:", required=True)
    pitting_oedema_lt = fields.Char(string="pitting oedema left:", required=True)
    non_pitting_oedema_lt = fields.Char(string="non-Pitting oedema left:", required=True)
    b_l_shin_discoloration_rashes_itching_bruises_lt = fields.Char(string="B/L shin discoloration/rashes/itching/bruises left:", required=True)
    local_knee_temperature_lt = fields.Char(string="Local knee temperature left:", required=True)
    heavy_free_lt = fields.Char(string="(heavy/free left:", required=True)
    complete_restricted_with_degree_lt = fields.Char(string="complete/restricted with degree left:", required=True)
    incomplete_extension_in_fingers_lt = fields.Char(string="incomplete extension in fingers left:", required=True)
    varus_valgus_deformity_lt = fields.Char(string="varus/valgus deformity left:", required=True)
    b_l_knee_crept_with_gradation_lt = fields.Char(string="B/L knee Crept with Gradation left:", required=True)
    pain_while_walking_reduced_by_lt = fields.Char(string="Pain Walking Reduced By % left:", required=True)
    b_l_slr_with_degree_lt = fields.Char(string="B/L SLR with degree left:", required=True)
    varicose_veins_gradation_lt = fields.Char(string="Varicose veins gradation left:", required=True)
    burning_sensation_in_b_l_knee_shin_lt = fields.Char(string="Burning sensation in B/L Knee/Shin left:", required=True)

    # Right Leg
    morning_stiffness_with_duration_rt = fields.Char(string="Morning stiffness with duration right:", required=True)
    b_l_shin_tenderness_with_gradation_rt = fields.Char(string="B/L shin tenderness with gradation right:", required=True)
    b_l_knee_tenderness_with_gradation_and_position_rt = fields.Char(
        string="B/L Knee tenderness with gradation and position right:", required=True)
    b_l_shin_oedema_with_gradation_rt = fields.Char(string="B/L shin oedema with gradation right:", required=True)
    pitting_oedema_rt = fields.Char(string="pitting oedema right:", required=True)
    non_pitting_oedema_rt = fields.Char(string="non-Pitting oedema right:", required=True)
    b_l_shin_discoloration_rashes_itching_bruises_rt = fields.Char(
        string="B/L shin discoloration/rashes/itching/bruises right:", required=True)
    local_knee_temperature_rt = fields.Char(string="Local knee temperature right:", required=True)
    heavy_free_rt = fields.Char(string="heavy/free right:", required=True)
    complete_restricted_with_degree_rt = fields.Char(string="complete/restricted with degree right:", required=True)
    incomplete_extension_in_fingers_rt = fields.Char(string="incomplete extension in fingers right:", required=True)
    varus_valgus_deformity_rt = fields.Char(string="varus/valgus deformity right:", required=True)
    b_l_knee_crept_with_gradation_rt = fields.Char(string="B/L knee Crept with Gradation right:", required=True)
    pain_while_walking_reduced_by_rt = fields.Char(string="Pain Walking Reduced By % right:", required=True)
    b_l_slr_with_degree_rt = fields.Char(string="B/L SLR with degree right:", required=True)
    varicose_veins_gradation_rt = fields.Char(string="Varicose veins gradation right:", required=True)
    burning_sensation_in_b_l_knee_shin_rt = fields.Char(string="Burning sensation in B/L Knee/Shin right:", required=True)
    others_s = fields.Text(string="Others:")

    # Koshthagata Examination
    jivha = fields.Selection([("saam", "Saam"),
                              ("ishat_saam", "Ishat Saam"),
                              ("niram", "Niram")], string="Jivha:", required=True)
    jwaranubhuti = fields.Char(string="Jwaranubhuti:", required=True)
    kshudha = fields.Selection([("samyak", "Samyak"),
                                ("manda", "Manda"),
                                ("vishama", "Vishama"),
                                ("tikshna", "Tikshna")], string="Kshudha:", required=True)
    kantha = fields.Char(string="Kantha-Uro-Udar Daha:", required=True)
    tiktamlodgar = fields.Char(string="Tiktamlodgar:", required=True)
    mala_aadhman_malabaddhata_sticky_drava = fields.Char(string="Mala/Aadhman/Malabaddhata/sticky/Drava/IBS:", required=True)
    mutra_naktamutrata_mutradaha = fields.Char(string="Mutra/Naktamutrata/Mutradaha:", required=True)
    rasa_dhatu_dushti_lakshane = fields.Char(string="Rasa Dhatu Dushi Lakshane:", required=True)
    nidra = fields.Selection([("ati", "Ati"),
                              ("alpa", "Alpa"),
                              ("nasha", "Nasha"),
                              ("samyak", "Samyak")], string="Nidra:", required=True)
    sweda = fields.Selection([("alpa", "Alpa"),
                              ("ati", "Ati"),
                              ("foul", "Foul"),
                              ("samyak", "Samyak")], string="Sweda:", required=True)
    others_k = fields.Text(string="Others:")
    active = fields.Boolean(default=True)

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

    @api.onchange('patient_id')
    def _onchange_patient_id_autofill_previous(self):
        if not self.patient_id:
            return

        domain = [('patient_id', '=', self.patient_id.id)]

        # exclude current record ONLY if it already exists
        if self._origin and self._origin.id:
            domain.append(('id', '!=', self._origin.id))

        last_followup = self.env['patient.followup'].search(
            domain,
            order='weekly_followup_date desc',
            limit=1
        )

        if not last_followup:
            return

        fields_to_copy = [
            'diagnosis',
            'k_c_o',
            'investigation_status',
            'case_under_discussion_with',
            'type_of_therapy',
        ]

        for field in fields_to_copy:
            self[field] = last_followup[field]

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