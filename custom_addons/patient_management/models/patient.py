from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import re
import uuid


class Patient(models.Model):
    _name = "clinic.patient"
    _description = "Clinic Patient"
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string="Full Name", required=True, tracking=True)  # Full name of patient
    age = fields.Integer(string="Age", required=True, tracking=True)  # Age of patient
    gender = fields.Selection([
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    ], string="Gender", required=True, tracking=True)  # Gender of patient
    phone = fields.Char(string="Phone", required=True, size=10,
                        tracking=True)  # Phone number of patient which cannot be duplicate
    email = fields.Char(string="Email", tracking=True)  # Email of patient if any
    address = fields.Text(string="Address", required=True, tracking=True)  # Address of patient

    mrn = fields.Char(string="Medical Record Number",
                      readonly=True, copy=False, index=True)  # Medical record number
    uuid = fields.Char(string="UUID", readonly=True, copy=False, index=True, default=lambda self: str(uuid.uuid4()))
    enroll_date = fields.Date(string="Enrollment Date",
                              default=lambda self: self._ist_date(),
                              copy=False, required=True)  # Enroll Date of patient

    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True, tracking=True
    )  # Clinic name
    base_clinic_id = fields.Many2one(
        'clinic.clinic',
        string="Sub Clinic",
        tracking=True,
        default=lambda self: self.env.context.get('default_clinic_id')
    )

    pain_types = fields.Char(string="Pain Types", tracking=True)

    pain_diabetes = fields.Boolean(string="Diabetes")
    pain_knee = fields.Boolean(string="Knee Pain")
    pain_spine = fields.Boolean(string="Spine Pain")
    pain_ra = fields.Boolean(string="RA")
    pain_ana = fields.Boolean(string="ANA")

    is_existing = fields.Boolean(string="Is Existing", tracking=True)

    patient_source = fields.Selection([
        ('facebook', 'Facebook'),
        ('instagram', 'Instagram'),
        ('youtube', 'Youtube'),
        ('event', 'Event'),
        ('sms', 'SMS'),
        ('doctor', 'Doctor Referred'),
        ('walkin', 'Walk-in'),
        ('referral', 'Referral')
    ], string="Patient Source")

    source_event = fields.Many2one('event.event', string="Event Name", domain=lambda self: [
        '|',
        '&',
        ('stage_id.name', '=', 'Event Done'),  # Only look at Event Done stage
        ('date_begin', '>=', fields.Datetime.now() - relativedelta(months=3)),  # Within 3 months
        ('name', 'in', ['Others', 'Old Event'])  # Always include your custom options
    ])

    source_event_name = fields.Char(related='source_event.name', string="Event Name Helper")
    manual_event_name = fields.Char(string="Type Event Name")

    treatment_status = fields.Selection([
        ('converted', 'Enrolled'),
        ('not_converted', 'Not Enrolled'),
        ('not_applicable', 'Not Applicable'),
    ], string="Treatment Status", tracking=True)

    not_converted_reasons = fields.Selection([
        ('cost_issue', 'Cost Issue'),
        ('wants_time_to_think', 'Wants Time to Think'),
        ('wants_second_opinion', 'Wants Second Opinion'),
        ('wants_to_discuss_at_home', 'Wants to Discuss at Home'),
        ('not_interested', 'Not Interested'),
        ('personal_reasons', 'Personal Reasons'),
        ('emi_rejected', 'EMI Rejected'),
        ('not_feasible', 'Not Feasible'),
        ('busy_schedule', 'Busy Schedule'),
        ('travel_issue', 'Travel Issue'),
        ('decision_maker_absent', 'Decision Maker Absent'),
        ('patient_absent', 'Patient absent'),
        ('time_issue', 'Time Issue'),
        ('left_without_consultation_due_to_more_waiting_time', 'Left Without Consultation Due To Waiting Time'),
        ('was_getting_late_didnt_wait_for_closure', "Was getting late, didn't wait for closure"),
        ('others', 'Others'),
    ], string="Not Enrolled Reasons", tracking=True)

    not_applicable_reasons = fields.Selection([
        ('ligament_tear', 'Complete ligament tear'),
        ('hiv_aids_hep', 'HIV/AIDS/Hepatitis B and C'),
        ('cancer_tb', 'Active Cancer/Active Tuberculosis'),
        ('dialysis', 'Dialysis'),
        ('fracture', 'Fracture'),
        ('congenital_deformity', 'Congenital bone deformity'),
        ('pregnant_lactating', 'Pregnant/Lactating females'),
        ('chronic_cva', 'Chronic CVA'),
        ('local_skin_issues', 'Local skin issues (Acute Psoriasis, Gangrene, Open wounds)'),
        ('liver_cirrhosis', 'Liver cirrhosis'),
        ('ascites', 'Ascites'),
        ('renal_failure', 'Renal failure'),
        ('patient_not_present', 'Patient not present'),
        ('long_distance', 'Long distance'),
        ('recent_paralysis', 'Recent history of paralysis'),
        ('bed_ridden', 'Bed ridden patient'),
        ('surgical_implants', 'Surgical implants'),
        ('dvt', 'DVT'),
        ('no_knee_spine_concern', 'No knee/spine related concern'),
        ('others', 'Others'),
    ], string="Not Applicable Reasons", tracking=True)

    other_reason = fields.Char(string="Specify Other Reason", tracking=True)
    treatment_updated_by = fields.Many2one('res.users', string="Updated by", readonly=True)

    @api.onchange('treatment_status')
    def _onchange_treatment_status(self):
        for record in self:
            if record.treatment_status:  # update the tracker
                record.treatment_updated_by = self.env.user
            else:
                record.treatment_updated_by = False

            if record.treatment_status == 'converted':
                record.not_converted_reasons = False
                record.not_applicable_reasons = False
                record.other_reason = False

            elif record.treatment_status == 'not_converted':
                # Wipe the medical reasons and custom text box
                record.not_applicable_reasons = False
                record.other_reason = False

            elif record.treatment_status == 'not_applicable':
                # Wipe the sales reasons and custom text box
                record.not_converted_reasons = False
                record.other_reason = False

    _sql_constraints = [
        # ('unique_phone', 'unique(phone)', '⚠️ This phone number is already registered!'), # Prevent creating records for same registered phone numbers
        ('unique_uuid', 'unique(uuid)', '⚠️ UUID must be unique!'),
    ]

    admin_id = fields.Many2one("res.users", string="Admin / BM",
                               required=True,
                               default=lambda self: self.env.user,
                               readonly=True)

    partner_id = fields.Many2one("res.partner", string="Customer", help="Link patient to POS/Invoices")

    pain_others = fields.Boolean(string="Other")
    others = fields.Char(string="Specify if Other", tracking=True)

    patient_status = fields.Selection([
        ('visit', 'Visit'),
        ('active', 'Active'),
        ('inactive', 'Inactive')
    ], string="Patient Status", default='visit', tracking=True)

    active = fields.Boolean(default=True)

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

    total_sessions = fields.Integer(
        string="Total Sessions",
        compute="_compute_total_sessions",
        store=True
    )

    remaining_sessions = fields.Integer(
        string="Remaining Sessions",
        compute="_compute_remaining_sessions",
        store=True
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

    @api.onchange('phone')
    def _onchange_phone_duplicate_check(self):
        """Warn user if phone number is already registered to other patients."""
        if self.phone and len(self.phone) == 10:
            domain = [('phone', '=', self.phone)]
            if self._origin.id:
                domain += [('id', '!=', self._origin.id)]

            duplicates = self.env['clinic.patient'].search(domain)
            if duplicates:
                lines = []
                for i, p in enumerate(duplicates, 1):
                    clinic_name = p.clinic_id.name or "Unknown Clinic"
                    lines.append(f"{i}. 👤 {p.name}  |  🏥 {clinic_name}")

                return {
                    'warning': {
                        'title': f'⚠️ Duplicate Phone Number ({len(duplicates)} found)',
                        'message': (
                                f"This phone number is already registered to:\n\n"
                                + "\n".join(lines)
                                + "\n\nPlease verify before proceeding."
                        ),
                    }
                }

    xray_ids = fields.One2many(
        'patient.xray',
        "patient_id",
        string="Grade"
    )

    attachment_ids = fields.One2many(
        "patient.attachment",
        "patient_id",
        string="Attachments"
    )

    latest_xray_grade = fields.Selection(
        [
            ('grade_0', 'Grade 0'),
            ('grade_1', 'Grade 1'),
            ('grade_2', 'Grade 2'),
            ('grade_3', 'Grade 3'),
            ('grade_4', 'Grade 4'),
        ],
        string="Latest X-Ray Grade",
        compute="_compute_latest_xray_grade",
        store=True
    )

    latest_xray_status = fields.Selection(
        [
            ("significant_positive", "Significant Positive"),
            ("mild_positive", "Mild Positive"),
            ("no_change", "No Change"),
            ("negative", "Negative"),
            ("baseline", "Baseline"), ],
        string="Latest X-Ray Status",
        compute="_compute_latest_xray_grade",
        store=True
    )

    first_xray_grade = fields.Selection(
        [
            ('grade_0', 'Grade 0'),
            ('grade_1', 'Grade 1'),
            ('grade_2', 'Grade 2'),
            ('grade_3', 'Grade 3'),
            ('grade_4', 'Grade 4'),
        ],
        string="First X-Ray Grade",
        compute="_compute_first_xray_grade",
        store=True
    )

    first_xray_day = fields.Selection(
        [
            ("5", "5th Day"),
            ("20", "20th Day"),
            ("40", "40th Day"),
            ("60", "60th Day"),
            ("80", "80th Day"),
        ],
        string="First X-Ray Day",
        compute="_compute_first_xray_grade",
        store=True
    )

    latest_xray_day = fields.Selection(
        [
            ("5", "5th Day"),
            ("20", "20th Day"),
            ("40", "40th Day"),
            ("60", "60th Day"),
            ("80", "80th Day"),
        ],
        string="First X-Ray Day",
        compute="_compute_latest_xray_grade",
        store=True
    )

    @api.depends("attachment_ids.file_type",
                 "attachment_ids.active",
                 "attachment_ids.create_date",
                 "attachment_ids.x_ray_grade",
                 "attachment_ids.x_ray_status",
                 "attachment_ids.x_ray_day", )
    def _compute_latest_xray_grade(self):
        for rec in self:
            # Filter only active X-Ray attachments
            xray_attachments = rec.attachment_ids.filtered(
                lambda a: a.file_type == 'xray' and a.active
            )

            if xray_attachments:
                # Pick latest by create_date
                latest = xray_attachments.sorted(
                    key=lambda a: a.create_date or fields.Datetime.now(),
                    reverse=True
                )[0]

                rec.latest_xray_grade = latest.x_ray_grade
                rec.latest_xray_status = latest.x_ray_status
                rec.latest_xray_day = latest.x_ray_day
            else:
                rec.latest_xray_grade = False
                rec.latest_xray_status = False
                rec.latest_xray_day = False

    @api.depends("attachment_ids.file_type",
                 "attachment_ids.active",
                 "attachment_ids.create_date",
                 "attachment_ids.x_ray_grade",
                 "attachment_ids.x_ray_status",
                 "attachment_ids.x_ray_day")
    def _compute_first_xray_grade(self):
        for rec in self:
            xray_attachments = rec.attachment_ids.filtered(
                lambda a: a.file_type == 'xray' and a.active
            )

            if xray_attachments:
                # Pick earliest by create_date (ascending)
                first = xray_attachments.sorted(
                    key=lambda a: a.create_date or fields.Datetime.now(),
                    reverse=False
                )[0]

                rec.first_xray_grade = first.x_ray_grade
                rec.first_xray_day = first.x_ray_day
            else:
                rec.first_xray_grade = False
                rec.first_xray_day = False

    @api.depends("enrollment_ids.total_sessions", "enrollment_ids.state", "enrollment_ids.active")
    def _compute_total_sessions(self):
        for rec in self:
            # Use active_test=False to include archived enrollments explicitly, then filter manually
            all_enrollments = self.env['patient.enrollment'].with_context(active_test=False).search([
                ('patient_id', '=', rec.id),
                ('active', '=', True),
                ('state', 'in', ['active', 'completed']),
            ])
            rec.total_sessions = sum(all_enrollments.mapped('total_sessions'))

    @api.depends("enrollment_ids.remaining_sessions", "enrollment_ids.state", "enrollment_ids.active")
    def _compute_remaining_sessions(self):
        for rec in self:
            all_enrollments = self.env['patient.enrollment'].with_context(active_test=False).search([
                ('patient_id', '=', rec.id),
                ('active', '=', True),
                ('state', 'in', ['active', 'completed']),
            ])
            rec.remaining_sessions = sum(all_enrollments.mapped('remaining_sessions'))

    @api.depends("enrollment_ids.state", "enrollment_ids.active", "enrollment_ids.payment_state",)
    def _compute_active_enrollment_id(self):
        for patient in self:
            all_enrollments = self.env['patient.enrollment'].with_context(active_test=False).search([
                ('patient_id', '=', patient.id),
                ('active', '=', True),
                ('state', '=', 'active'),
                ('payment_state', '=', 'paid'),
            ])
            patient.active_enrollment_id = all_enrollments[:1] if all_enrollments else False

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

        # if 'patient_status' in vals:
        #     for rec in self:
                # if not self.env.su and not self.env.context.get('from_cron'):
                #     if rec.patient_status == 'active' and vals.get('patient_status') == 'inactive':
                #         raise ValidationError(
                #             _("You cannot manually set patient to Inactive. It is system controlled.")
                #         )
                # Prevent changing back to 'Visit'
                # if rec.patient_status in ['active', 'inactive'] and vals.get('patient_status') == 'visit':
                #     raise ValidationError(
                #         _("Patient status cannot be changed back to 'Visit' once it is Active or Inactive."))

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

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))

    def _cron_check_inactive_patients(self):
        today = fields.Date.today()
        patients = self.search([('patient_status', '=', 'active')])

        for patient in patients:

            # 1. If they have no remaining sessions, they should be inactive immediately
            if patient.remaining_sessions <= 0:
                patient.with_context(from_cron=True).sudo().write({'patient_status': 'inactive'})
                continue

            # 2. Check for the most recent session
            last_session = self.env['patient.session'].search([
                ('patient_id', '=', patient.id),
                ('active', '=', True)
            ], order='session_date desc', limit=1)

            # 3. Determine the exact date to compare against
            last_date = False
            if last_session and last_session.session_date:
                last_date = last_session.session_date
            elif patient.active_enrollment_id:
                # Fallback: They paid but haven't taken a session yet
                last_date = patient.active_enrollment_id.enrollment_date
            else:
                # Absolute fallback if no enrollment is found
                last_date = patient.enroll_date

            # 4. Calculate difference and update status
            if last_date:
                diff_days = (today - last_date).days

                # Use >= 7 to be consistent across both sessions and enrollments
                if diff_days > 7:
                    patient.with_context(from_cron=True).sudo().write({
                        'patient_status': 'inactive'
                    })

    @api.constrains('enroll_date')
    def _check_visit_date(self):
        today = date.today()
        for record in self:
            if record.enroll_date and record.enroll_date > today:
                raise ValidationError(
                    _("The visit date must be today or earlier.")
                )

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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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
            "context": {"default_patient_id": self.id, "active_test": not show_all, },
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

    def action_open_consent(self):
        return {
            'name': 'Consent Form',
            'type': 'ir.actions.act_window',
            'res_model': 'consent.form',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_patient_id': self.id,
            },
        }

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

    @api.onchange('patient_source')
    def _onchange_patient_source_clear_events(self):
        """Wipe event fields if the source is changed away from 'Event'"""
        for record in self:
            if record.patient_source != 'event':
                record.source_event = False
                record.manual_event_name = False

    @api.onchange('source_event')
    def _onchange_source_event_clear_manual(self):
        """Wipe the manual text box if they select a standard event instead of Others/Old Event"""
        for record in self:
            if record.source_event_name not in ('Others', 'Old Event'):
                record.manual_event_name = False