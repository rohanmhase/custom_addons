from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re
from datetime import datetime, timedelta

class Patient(models.Model):
    _name = "clinic.patient"
    _description = "Clinic Patient"


    name = fields.Char(string="Full Name", required=True)               # Full name of patient
    age = fields.Integer(string="Age", required=True)                   # Age of patient
    gender = fields.Selection([
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ], string="Gender", required=True)                                  # Gender of patient
    phone = fields.Char(string="Phone", required=True, maxlength=10)    # Phone number of patient which cannot be duplicate
    email = fields.Char(string="Email")                                 # Email of patient if any
    address = fields.Text(string="Address", required=True)              # Address of patient

    mrn = fields.Char(string="Medical Record Number",
                      readonly=True, copy=False, index=True)            # Medical record number
    enroll_date = fields.Date(string="Enrollment Date",
                              readonly=True,
                              default=lambda self: self._ist_date(),
                              copy=False, required=True)                # Enroll Date of patient

    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True,
    )                                                                   # Clinic name

    _sql_constraints = [
        ('unique_phone', 'unique(phone)', '⚠️ This phone number is already registered!'),
    ]

    admin_id = fields.Many2one("res.users", string="Admin / BM",
                                required=True,
                                default=lambda self: self.env.user,
                                readonly=True)

    partner_id = fields.Many2one("res.partner", string="Customer", help="Link patient to POS/Invoices")

    # -------------------------------
    # Create Override
    # -------------------------------

    @api.constrains('phone')
    def _check_phone_number(self):
        for rec in self:
            if rec.phone:
                # Only allow exactly 10 digits
                if not re.match(r'^\d{10}$', rec.phone):
                    raise ValidationError("Phone number must be exactly 10 digits.")


    @api.model
    def create(self, vals):
        # Auto-Create partner if not given,
        if not vals.get("partner_id") and vals.get("name"):
            partner = self.env["res.partner"].create({
                "name": vals["name"],
                "phone": vals.get("phone"),
            })
            vals["partner_id"] = partner.id

        # Generate MRN based on clinic code + year + sequence
        clinic_id = vals.get("clinic_id")
        if clinic_id:
            clinic = self.env["clinic.clinic"].browse(clinic_id)
            if not clinic.code:
                raise ValidationError("⚠️ Selected clinic has no code defined!")

            current_year = datetime.today().year
            prefix = f"{clinic.code}-{current_year}"

            # Find last MRN only for this clinic and this year
            last_patient = self.env["clinic.patient"].search(
                [("clinic_id", "=", clinic_id), ("mrn", "like", prefix + "-")],
                order="mrn desc",
                limit=1,
            )

            if last_patient and last_patient.mrn:
                match = re.search(rf"{prefix}-(\d+)", last_patient.mrn)
                next_num = int(match.group(1)) + 1 if match else 1
            else:
                next_num = 1

            vals["mrn"] = f"{prefix}-{str(next_num).zfill(3)}"

        return super(Patient, self).create(vals)

    # -------------------------------
    # Write Override
    # -------------------------------
    def write(self, vals):
        # MRN should never be modified
        if "mrn" in vals:
            raise ValidationError("⚠️ MRN cannot be modified!")

        # Restrict changes to core patient info after creation
        restricted_fields = {
            "name", "age", "gender", "phone", "address", "clinic_id"
        }
        if any(field in vals for field in restricted_fields):
            raise ValidationError("⚠️ You cannot update patient details once saved!")

        return super(Patient, self).write(vals)

    def action_open_blood_report(self):
        """Open Blood Report related to this patient"""
        return {
            "type": "ir.actions.act_window",
            "name": "Blood Report",
            "res_model": "patient.blood_report",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def action_open_case_taking(self):
        """Open Case Taking related to this patient"""
        return {
            "type": "ir.actions.act_window",
            "name": "Case Taking",
            "res_model": "patient.case_taking",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def action_open_daily_followup(self):
        """Open Daily Followup related to this patient"""
        return {
            "type": "ir.actions.act_window",
            "name": "Daily Followup",
            "res_model": "patient.daily_followup",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def action_open_diet_chart(self):
        """Open Diet Chart related to this patient"""
        return {
            "type": "ir.actions.act_window",
            "name": "Diet Chart",
            "res_model": "patient.diet_chart",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def action_open_followup(self):
        """Open Weekly Followup related to this patient"""
        return {
            "type": "ir.actions.act_window",
            "name": "Followup",
            "res_model": "patient.followup",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()