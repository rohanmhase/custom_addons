from odoo import fields, models, api
from datetime import datetime, timedelta


class DietChart(models.Model):
    _name = "patient.diet_chart"
    _description = "Diet Chart"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    diet_taken_date = fields.Date(string="Date", default=lambda self: self._ist_date(), readonly=True, required=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, readonly=True,
                                default=lambda self: self.env.user)
    therapy_day = fields.Char(string="Therapy Day", required=True)
    morning_with_time = fields.Char(string="Morning With Time")
    lunch_with_time = fields.Char(string="Lunch with Time")
    evening_with_time = fields.Char(string="Evening With Time")
    dinner_with_time = fields.Char(string="Dinner With Time")
    comments = fields.Char(string="Comments")
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