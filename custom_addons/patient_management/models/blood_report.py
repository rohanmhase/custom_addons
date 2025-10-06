from odoo import models, fields, api
from datetime import datetime, timedelta


class BloodReport(models.Model):
    _name = 'patient.blood_report'
    _description = 'Blood Report'

    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True, readonly=True)
    doctor_id = fields.Many2one('res.users', string='Doctor', required=True, readonly=True, default=lambda self: self.env.user)
    blood_report_date = fields.Date(string='Blood Report Date', required=True, readonly=True, default=lambda self: self._ist_date())
    blood_report_day = fields.Selection([("1", "1st Day"),
                                         ("30", "30th Day"),
                                         ("60", "60th Day"),
                                         ("80", "80th Day")],
                                        string="Day of Blood Report", required=True)

    haemoglobin = fields.Char(string="Haemoglobin")
    esr = fields.Char(string="ESR")
    platelet_count = fields.Char(string="Platelet Count")
    bsl_fasting = fields.Char(string="BSL Fasting")
    bsl_post_prandial = fields.Char(string="BSL Post Prandial")
    hba1c = fields.Char(string="HBA1c")
    sr_uric_acid = fields.Char(string="Sr. Uric Acid")
    ra_factor_titre = fields.Char(string="Ra Factor/Titre")
    crp = fields.Char(string="CRP")
    ana = fields.Char(string="ANA")
    t_cholesterol = fields.Char(string="T-Cholesterol")
    t_triglyceride = fields.Char(string="T-Triglyceride")
    sr_creatinine = fields.Char(string="Sr. Creatinine")
    tsh = fields.Char(string="TSH")
    urine_sugar = fields.Char(string="Urine Sugar")
    urine_pus_cells_bacteria = fields.Char(string="Urine pus cells/bacteria")
    urine_protein = fields.Char(string="Urine Protein")
    urine_blood_crystal = fields.Char(string="Urine blood/crystal")
    cbc = fields.Char(string="CBC")
    lft = fields.Char(string="LFT")
    rft = fields.Char(string="RFT")
    lipid_profile = fields.Char(string="Lipid Profile")
    t3 = fields.Char(string="T3")
    t4 = fields.Char(string="T4")
    uric_acid = fields.Char(string="Uric Acid")
    ra = fields.Char(string="Ra Factor")
    anti_ccp = fields.Char(string="Anti-CCP")
    homa_ir = fields.Char(string="HOMA-IR")
    c_peptide = fields.Char(string="C-Peptide")
    la_ma_test = fields.Char(string="La/Ma Test")
    urine_routine = fields.Char(string="Urine Routine")
    urine_microscopic = fields.Char(string="Urine Microscopic")
    notes = fields.Char(string="Notes")
    active = fields.Boolean(default=True)


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
