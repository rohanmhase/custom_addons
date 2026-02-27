from odoo import models, fields, api, _
from datetime import datetime, timedelta

class Gradation(models.Model):
    _name = "patient.gradation"
    _description = "Gradation"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user,
                                readonly=True)
    gradation_date = fields.Date(string="Date", required=True, default=lambda self: self._ist_date(),
                                       readonly=True)

    is_left_knee = fields.Boolean(string="Left Knee", default=False)
    is_right_knee = fields.Boolean(string="Right Knee", default=False)

    Grade_1 = [('0', '0'), ('1', '1'),('2', '2'),('3', '3'),('4', '4'),]
    Grade_2 = [('0', '0'), ('1', '1'), ('2', '2'), ('3', '3'), ('4', '4'), ('5', '5'),]

    left_knee_tenderness = fields.Selection(string="Tenderness", selection=Grade_1,)
    right_knee_tenderness = fields.Selection(string="Tenderness", selection=Grade_1,)
    left_knee_morning_stiffness = fields.Selection(string="Morning Stiffness", selection=Grade_2,)
    right_knee_morning_stiffness = fields.Selection(string="Morning Stiffness", selection=Grade_2, )
    left_knee_swelling = fields.Selection(string="Swelling", selection=Grade_1,)
    right_knee_swelling = fields.Selection(string="Swelling", selection=Grade_1,)

    @api.onchange('is_left_knee')
    def _onchange_is_left_knee(self):
        if self.is_left_knee:
            self.is_right_knee = False

    @api.onchange('is_right_knee')
    def _onchange_is_right_knee(self):
        if self.is_right_knee:
            self.is_left_knee = False

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()


