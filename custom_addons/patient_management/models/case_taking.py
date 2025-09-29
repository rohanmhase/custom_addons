from odoo import models, fields, api
from datetime import datetime, timedelta


class CaseTaking(models.Model):
    _name = "patient.case_taking"
    _description = "Case Taking"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user, readonly=True)
    case_taking_date = fields.Date(string="Case Taking Date", required=True, default=lambda self: self._ist_date(), readonly=True)
    k_c_o = fields.Char(string="K/C/O", required=True) # Known case of
    p_h_o = fields.Char(string="P/H/O", required=True) # Past history of
    s_h = fields.Char(string="Sx/H", required=True) # Surgical history
    f_h = fields.Char(string="F/H", required=True) # Family history
    allergies = fields.Char(string="Allergies", required=True)

    habits = fields.Char(string="Habits", required=True)
    mal = fields.Char(string="Mal", required=True)
    mutra = fields.Char(string="Mutra", required=True)
    nakta = fields.Char(string="Nakta", required=True)
    kshudha = fields.Char(string="Kshudha", required=True)
    nidra = fields.Char(string="Nidra", required=True)
    jivha = fields.Char(string="Jivha", required=True)
    crepts_rt = fields.Char(string="Crepts Right", required=True)
    crepts_lt = fields.Char(string="Crepts Left", required=True)
    shin_tenderness_rt = fields.Char(string="Shin Tenderness Right", required=True)
    shin_tenderness_lt = fields.Char(string="Shin Tenderness Left", required=True)
    swelling_rt = fields.Char(string="Swelling Right", required=True)
    swelling_lt = fields.Char(string="Swelling Left", required=True)
    rom_rt = fields.Char(string="ROM Right", required=True)
    rom_lt = fields.Char(string="ROM Left", required=True)
    slr_rt = fields.Char(string="SLR Right", required=True)
    slr_lt = fields.Char(string="SLR Left", required=True)
    pedal_oedema_rt = fields.Char(string="Pedal Oedema Right", required=True)
    pedal_oedema_lt = fields.Char(string="Pedal Oedema Left", required=True)
    pain_rt = fields.Char(string="Pain Right", required=True)
    pain_lt = fields.Char(string="Pain Left", required=True)
    deformity_rt = fields.Char(string="Deformity Right", required=True)
    deformity_lt = fields.Char(string="Deformity Left", required=True)
    diet = fields.Char(string="Diet", required=True)
    diagnosis = fields.Char(string="Diagnosis", required=True)
    adv_investigation = fields.Char(string="Adv Investigation", required=True)
    adv_treatment = fields.Char(string="Adv Treatment", required=True)
    adv_rx = fields.Char(string="Advise Rx", required=True)
    treatment = fields.Char(string="Treatment", required=True)

    _sql_constraints = [("unique_patient_case_taking", "unique(patient_id)", "⚠️ A patient can only have one case taking!")]

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date
