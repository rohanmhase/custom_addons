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

    # Shakhagata Examinations

    # Left Leg
    morning_stiffness_with_duration_lt = fields.Char(string="1. Morning stiffness with duration:", required=True)
    b_l_shin_tenderness_with_gradation_lt = fields.Char(string="2. B/L shin tenderness with gradation:", required=True)
    b_l_knee_tenderness_with_gradation_and_position_lt = fields.Char(
        string="3. B/L Knee tenderness with gradation and position:", required=True)
    b_l_shin_oedema_with_gradation_lt = fields.Char(string="4. B/L shin oedema with gradation:", required=True)
    pitting_oedema_lt = fields.Char(string="(a) pitting oedema:", required=True)
    non_pitting_oedema_lt = fields.Char(string="(b) non-Pitting oedema:", required=True)
    b_l_shin_discoloration_rashes_itching_bruises_lt = fields.Char(string="5. B/L shin discoloration/rashes/itching/bruises:", required=True)
    local_knee_temperature_lt = fields.Char(string="6. Local knee temperature:", required=True)
    heavy_free_lt = fields.Char(string="(a) heavy/free:", required=True)
    complete_restricted_with_degree_lt = fields.Char(string="(b) complete/restricted with degree:", required=True)
    incomplete_extension_in_fingers_lt = fields.Char(string="(c) incomplete extension in fingers:", required=True)
    varus_valgus_deformity_lt = fields.Char(string="(d) varus/valgus deformity:", required=True)
    b_l_knee_crept_with_gradation_lt = fields.Char(string="8. B/L knee Crept with Gradation:", required=True)
    pain_while_walking_reduced_by_lt = fields.Char(string="9. Pain Walking Reduced By %:", required=True)
    b_l_slr_with_degree_lt = fields.Char(string="10. B/L SLR with degree:", required=True)
    varicose_veins_gradation_lt = fields.Char(string="11. Varicose veins gradation:", required=True)
    burning_sensation_in_b_l_knee_shin_lt = fields.Char(string="12. Burning sensation in B/L Knee/Shin:", required=True)

    # Right Leg
    morning_stiffness_with_duration_rt = fields.Char(string="1. Morning stiffness with duration:", required=True)
    b_l_shin_tenderness_with_gradation_rt = fields.Char(string="2. B/L shin tenderness with gradation:", required=True)
    b_l_knee_tenderness_with_gradation_and_position_rt = fields.Char(
        string="3. B/L Knee tenderness with gradation and position:", required=True)
    b_l_shin_oedema_with_gradation_rt = fields.Char(string="4. B/L shin oedema with gradation:", required=True)
    pitting_oedema_rt = fields.Char(string="(a) pitting oedema:", required=True)
    non_pitting_oedema_rt = fields.Char(string="(b) non-Pitting oedema:", required=True)
    b_l_shin_discoloration_rashes_itching_bruises_rt = fields.Char(
        string="5. B/L shin discoloration/rashes/itching/bruises:", required=True)
    local_knee_temperature_rt = fields.Char(string="6. Local knee temperature:", required=True)
    heavy_free_rt = fields.Char(string="(a) heavy/free:", required=True)
    complete_restricted_with_degree_rt = fields.Char(string="(b) complete/restricted with degree:", required=True)
    incomplete_extension_in_fingers_rt = fields.Char(string="(c) incomplete extension in fingers:", required=True)
    varus_valgus_deformity_rt = fields.Char(string="(d) varus/valgus deformity:", required=True)
    b_l_knee_crept_with_gradation_rt = fields.Char(string="8. B/L knee Crept with Gradation:", required=True)
    pain_while_walking_reduced_by_rt = fields.Char(string="9. Pain Walking Reduced By %:", required=True)
    b_l_slr_with_degree_rt = fields.Char(string="10. B/L SLR with degree:", required=True)
    varicose_veins_gradation_rt = fields.Char(string="11. Varicose veins gradation:", required=True)
    burning_sensation_in_b_l_knee_shin_rt = fields.Char(string="12. Burning sensation in B/L Knee/Shin:", required=True)
    others_s = fields.Text(string="Others:")

    # Koshthagata Examination
    jivha = fields.Char(string="Jivha:", required=True)
    jwaranubhuti = fields.Char(string="Jwaranubhuti:", required=True)
    kshudha = fields.Char(string="Kshudha (manda/vishama/tikshna):", required=True)
    kantha = fields.Char(string="Kantha-Uro-Udar Daha:", required=True)
    tiktamlodgar = fields.Char(string="Tiktamlodgar:", required=True)
    mala_aadhman_malabaddhata_sticky_drava = fields.Char(string="Mala/Aadhman/Malabaddhata/sticky/Drava/IBS:", required=True)
    mutra_naktamutrata_mutradaha = fields.Char(string="Mutra/Naktamutrata/Mutradaha:", required=True)
    rasa_dhatu_dushti_lakshane = fields.Char(string="Rasa Dhatu Dushi Lakshane:", required=True)
    nidra = fields.Char(string="Nidra (Ati/Alpa/Nasha):", required=True)
    sweda = fields.Char(string="Sweda (Alpa/Ati/foul):", required=True)
    others_k = fields.Text(string="Others:")

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date