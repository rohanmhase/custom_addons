from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta

class DailyFollowup(models.Model):
    _name = "patient.daily_followup"
    _description = "Daily Followup Details"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    followup_date = fields.Date(string="Date", default= lambda self: self._ist_date(), readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user,
                                readonly=True)
    c_o = fields.Text(string="C/O", required=True)

    crepts_rt = fields.Char(string="Crepts Right")
    crepts_lt = fields.Char(string="Crepts Left")
    shin_tenderness_rt = fields.Char(string="Shin Tenderness Right")
    shin_tenderness_lt = fields.Char(string="Shin Tenderness Left")
    swelling_rt = fields.Char(string="Swelling Right")
    swelling_lt = fields.Char(string="Swelling Left")
    rom_rt = fields.Char(string="ROM Right")
    rom_lt = fields.Char(string="ROM Left")
    slr_rt = fields.Char(string="SLR Right")
    slr_lt = fields.Char(string="SLR Left")
    pedal_oedema_rt = fields.Char(string="Pedal Oedema Right")
    pedal_oedema_lt = fields.Char(string="Pedal Oedema Left")
    pain_rt = fields.Char(string="Pain Right")
    pain_lt = fields.Char(string="Pain Left")
    deformity_rt = fields.Char(string="Deformity Right")
    deformity_lt = fields.Char(string="Deformity Left")

    mal = fields.Char(string="Mal")
    mutra = fields.Char(string="Mutra")
    nakta = fields.Char(string="Nakta")
    kshudha = fields.Char(string="Kshudha")
    nidra = fields.Char(string="Nidra")
    jivha = fields.Char(string="Jivha")

    notes = fields.Text(string="Notes")

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date


