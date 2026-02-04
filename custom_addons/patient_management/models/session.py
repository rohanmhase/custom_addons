from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta


class Session(models.Model):
    _name = "patient.session"
    _description = "Session Details"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    session_date = fields.Date(string="Session Date", default=lambda self: self._ist_date(), readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user,
                                readonly=True)
    session_day = fields.Integer(
        string="Session Day", compute="default_get", store=True)
    jivha = fields.Selection([("saam", "Saam"),
                              ("ishat_saam", "Ishat Saam"),
                              ("niram", "Niram")], string="Jivha", required=True)
    swelling = fields.Char(string="Swelling", required=True)
    digestion = fields.Selection([("samyak", "Samyak"),
                                ("manda", "Manda"),
                                ("vishama", "Vishama"),
                                ("tikshna", "Tikshna")], string="Digestion", required=True)
    motion = fields.Char(string="Motion", required=True)
    detox_therapy = fields.Char(string="Detox Therapy", required=True)
    regeneration_therapy = fields.Char(string="Regeneration Therapy", required=True)
    left_knee = fields.Char(string="Left Knee", required=True)
    right_knee = fields.Char(string="Right Knee", required=True)
    before_and_after_therapy_comment = fields.Char(string="Before & After Therapy Comment", required=True)
    therapist_name = fields.Char(string="Therapist Name", required=True)
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")
    session_type = fields.Selection([
        ('clinic', 'Clinic'),
        ('home', 'Home'),
        ('self', 'Self'),
    ], string="Therapy Location", required=True)
    morning_with_time = fields.Char(string="7 AM - 9 AM", required=True)
    lunch_with_time = fields.Char(string="10 AM - 1 PM", required=True)
    evening_with_time = fields.Char(string="4 PM - 6 PM", required=True)
    dinner_with_time = fields.Char(string="7 PM - 10 PM", required=True)
    comments = fields.Char(string="Comments")
    active = fields.Boolean(default=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        patient_id = self.env.context.get("default_patient_id")
        if patient_id:
            # Fetch all enrollments of the patient
            enrollments = self.env["patient.enrollment"].sudo().search([
                ("patient_id", "=", patient_id),
                ("active", "=", True)  # or remove this to include old deleted ones too
            ])

            # Sum used sessions from all enrollments
            total_used = sum(enrollments.mapped("used_sessions"))

            # New session day = total used sessions + 1
            res["session_day"] = total_used + 1
        else:
            res["session_day"] = 1

        return res

    @api.model
    def create(self, vals):
        # Get the patient either from vals or context
        patient_id = vals.get("patient_id") or self.env.context.get("default_patient_id")
        if not patient_id:
            raise UserError("Patient is required to create a session.")

        patient = self.env["clinic.patient"].browse(patient_id)

        # Re-fetch the enrollment from DB to get latest values
        enrollment = self.env["patient.enrollment"].sudo().search([
            ("id", "=", patient.active_enrollment_id.id),
            ("state", "=", "active"),
            ("active", "=", "true")
        ], limit=1, order="id asc")

        if not enrollment:
            raise UserError("This patient has no active enrollment. Kindly contact with BM / Admin for further extension.")

        if enrollment.remaining_sessions <= 0:
            raise UserError("This patient has no active enrollment. Kindly contact with BM / Admin for further extension.")

        # Create the session
        session = super().create(vals)

        # Increment used_sessions immediately to prevent overbooking
        enrollment.sudo().write({"used_sessions": enrollment.used_sessions + 1})

        return session

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