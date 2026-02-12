from odoo import models, fields, api
from datetime import timedelta, datetime


class PatientXRay(models.Model):
    _name = "patient.xray"
    _description = "Patient X-Ray"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, readonly=True,
                                default=lambda self: self.env.user)
    date_taken = fields.Date(string="X-Ray Date", required=True, readonly=True, default=lambda self: self._ist_date())
    x_ray_day = fields.Selection([("5", "5th Day"),
                                  ("20", "20th Day"),
                                  ("40", "40th Day"),
                                  ("60", "60th Day"),
                                  ("80", "80th Day")],
                                 string="Day of X-Ray", required=True)
    x_ray_actual_date = fields.Date(string="Date", required=True)

    grade = fields.Selection([
        ('grade_0', 'Grade 0'),
        ('grade_1', 'Grade 1'),
        ('grade_2', 'Grade 2'),
        ('grade_3', 'Grade 3'),
        ('grade_4', 'Grade 4'),
    ], string="Grade", required=True)

    x_ray_status = fields.Selection([("significant_positive", "Significant Positive"),
                                     ("mild_positive", "Mild Positive"),
                                     ("no_change", "No Change"),
                                     ("negative", "Negative"),], string="X-Ray Status", required=True)

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
