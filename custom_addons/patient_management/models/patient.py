from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
import re
from datetime import datetime, timedelta
import uuid

class Patient(models.Model):
    _name = "clinic.patient"
    _description = "Clinic Patient"
    _inherit = ['mail.thread', 'mail.activity.mixin']


    name = fields.Char(string="Full Name", required=True, tracking=True)               # Full name of patient
    age = fields.Integer(string="Age", required=True, tracking=True)                   # Age of patient
    gender = fields.Selection([
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ], string="Gender", required=True, tracking=True)                                  # Gender of patient
    phone = fields.Char(string="Phone", required=True, size=10, tracking=True)         # Phone number of patient which cannot be duplicate
    email = fields.Char(string="Email", tracking=True)                                 # Email of patient if any
    address = fields.Text(string="Address", required=True, tracking=True)              # Address of patient

    mrn = fields.Char(string="Medical Record Number",
                      readonly=True, copy=False, index=True)            # Medical record number
    uuid = fields.Char(string="UUID", readonly=True, copy=False, index=True, default=lambda self: str(uuid.uuid4()))
    enroll_date = fields.Date(string="Enrollment Date",
                              default=lambda self: self._ist_date(),
                              copy=False, required=True)                # Enroll Date of patient

    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True, tracking=True
    )                                                                   # Clinic name

    pain_types = fields.Char(string="Pain Types", tracking=True)

    pain_diabetes = fields.Boolean(string="Diabetes")
    pain_knee = fields.Boolean(string="Knee Pain")
    pain_spine = fields.Boolean(string="Spine Pain")
    pain_ra = fields.Boolean(string="RA")
    pain_ana = fields.Boolean(string="ANA")

    is_existing = fields.Boolean(string="Is Existing", tracking=True)

    _sql_constraints = [
        # ('unique_phone', 'unique(phone)', '⚠️ This phone number is already registered!'), # Prevent creating records for same registered phone numbers
        ('unique_uuid', 'unique(uuid)', '⚠️ UUID must be unique!'),
    ]

    admin_id = fields.Many2one("res.users", string="Admin / BM",
                                required=True,
                                default=lambda self: self.env.user,
                                readonly=True)

    partner_id = fields.Many2one("res.partner", string="Customer", help="Link patient to POS/Invoices")

    enrollment_ids = fields.One2many(
        "patient.enrollment",
        "patient_id",
        string="Enrollment",
    )

    active_enrollment_id = fields.Many2one(
        "patient.enrollment",
        string="Active Enrollment",
        compute="_compute_active_enrollment_id",
        store=True,
    )

    remaining_sessions = fields.Integer(
        string="Remaining Sessions",
        compute="_compute_remaining_sessions",
        store=False
    )

    session_ids = fields.One2many(
        "patient.session",
        "patient_id",
        string="Therapy Sessions",
    )

    @api.model
    def _get_pain_options(self):
        return [
            ('knee', 'Knee Pain'),
            ('spine', 'Spine Pain'),
            ('diabetes', 'Diabetes'),
            ('ra', 'RA'),
            ('ana', 'ANA')
        ]

    @api.onchange('pain_diabetes', 'pain_knee', 'pain_spine', 'pain_ra', 'pain_ana')
    def _onchange_pain_types(self):
        selected = []
        if self.pain_knee:
            selected.append("Knee Pain")
        if self.pain_spine:
            selected.append("Spine Pain")
        if self.pain_diabetes:
            selected.append("Diabetes")
        if self.pain_ra:
            selected.append("RA")
        if self.pain_ana:
            selected.append("ANA")
        self.pain_types = ", ".join(selected)


    def _compute_remaining_sessions(self):
        for rec in self:
            enrollment = rec.active_enrollment_id
            rec.remaining_sessions = enrollment.remaining_sessions if enrollment else 0

    @api.depends("enrollment_ids.state")
    def _compute_active_enrollment_id(self):
        for patient in self:
            active = patient.enrollment_ids.filtered(lambda r: r.state == "active")
            patient.active_enrollment_id = active[:1] if active else False


    @api.constrains('phone')
    def _check_phone_number(self):
        for rec in self:
            if rec.phone:
                # Only allow exactly 10 digits
                if not re.match(r'^\d{10}$', rec.phone):
                    raise ValidationError("Phone number must be exactly 10 digits and contain only numbers.")

    @api.model
    def create(self, vals):
        # Auto-Create partner if not given,
        if not vals.get("partner_id") and vals.get("name"):
            partner = self.env["res.partner"].create({
                "name": vals["name"],
                "phone": vals.get("phone"),
                "email": vals.get("email"),
                "street": vals.get("address"),
                "clinic_id": vals.get("clinic_id"),  # link clinic
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

            seq = self.env["ir.sequence"].next_by_code("clinic.patient.mrn") or "000001"
            vals["mrn"] = f"{prefix}-{seq.zfill(6)}"


        return super(Patient, self).create(vals)

    # -------------------------------
    # Write Override
    # -------------------------------
    def write(self, vals):
        if "mrn" in vals:
            raise ValidationError("⚠️ MRN cannot be modified!")

        res = super(Patient, self).write(vals)

        # Sync changes with the linked partner record
        for rec in self:
            if rec.partner_id:
                partner_vals = {}
                if "name" in vals:
                    partner_vals["name"] = rec.name
                if "phone" in vals:
                    partner_vals["phone"] = rec.phone
                if "email" in vals:
                    partner_vals["email"] = rec.email
                if "address" in vals:
                    # assuming address in patient is stored in partner.street
                    partner_vals["street"] = rec.address
                if "clinic_id" in vals:
                    partner_vals["clinic_id"] = rec.clinic_id.id

                if partner_vals:
                    rec.partner_id.write(partner_vals)

        return res

    def action_open_blood_report(self):
        """Open Blood Report related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')

        return {
            "type": "ir.actions.act_window",
            "name": "Blood Report",
            "res_model": "patient.blood_report",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_case_taking(self):
        """Open Case Taking related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Case Taking",
            "res_model": "patient.case_taking",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_daily_followup(self):
        """Open Daily Followup related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Daily Followup",
            "res_model": "patient.daily_followup",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_diet_chart(self):
        """Open Diet Chart related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Diet Chart",
            "res_model": "patient.diet_chart",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_followup(self):
        """Open Weekly Followup related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Followup",
            "res_model": "patient.followup",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_enrollment(self):
        """Open Enrollment related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Enrollment",
            "res_model": "patient.enrollment",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_session(self):
        """Open Daily Therapy Session related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Therapy Session",
            "res_model": "patient.session",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_xray(self):
        """Open X-Rays related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "X-Rays",
            "res_model": "patient.xray",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_prescription(self):
        """Open Prescription related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Prescription",
            "res_model": "patient.prescription",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_attachment(self):
        """Open Prescription related to this patient"""
        user = self.env.user
        show_all = user.has_group('clinic_management.group_show_inactive_reports')
        return {
            "type": "ir.actions.act_window",
            "name": "Attachment",
            "res_model": "patient.attachment",
            "view_mode": "tree,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id, "active_test": not show_all,},
        }

    def action_open_patient_case_paper(self):
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        url = f"{base_url}/patient/{self.uuid}"
        return {
            "type": "ir.actions.act_url",
            "target": "new",  # opens in new browser tab
            "url": url,
        }

    def action_open_patient_xray(self):
        self.ensure_one()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        url = f"{base_url}/patient/xray/{self.uuid}"
        return {
            "type": "ir.actions.act_url",
            "target": "new",  # opens in new browser tab
            "url": url,
        }

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()