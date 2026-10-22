from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta
from odoo.exceptions import ValidationError


class Session(models.Model):
    _name = "patient.session"
    _description = "Session Details"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    enrollment_id = fields.Many2one(
        "patient.enrollment",
        string="Enrollment",
        readonly=True,
        copy=False,
        index=True,
        ondelete="restrict",
        help="Enrollment whose used-session count was consumed by this therapy session.",
    )
    session_date = fields.Date(string="Session Date", default=lambda self: self._ist_date(), readonly=True, index=True)
    doctor_id = fields.Many2one("res.users", string="Doctor", required=True, default=lambda self: self.env.user,
                                readonly=True)
    type_of_therapy = fields.Selection([("detox", "Detox"),
                                        ("regrowth", "Regrowth"),
                                        ], string="Type of Therapy", required=True)
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
    therapist_name = fields.Char(string="Therapist Name")
    therapist_id = fields.Many2one('clinic.therapist', string="Therapist Name")
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")
    session_type = fields.Selection([
        ('clinic', 'Clinic'),
        ('home', 'Home'),
        ('self', 'Self'),
    ], string="Therapy Location", required=True)
    clinic_id = fields.Many2one(
        'clinic.clinic',
        related='patient_id.clinic_id',
        string="Clinic Context"
    )
    therapy_clinic_id = fields.Many2one(
        'clinic.clinic',
        string="Therapy Clinic",
        readonly=True,
        store=True
    )
    morning_with_time = fields.Char(string="7 AM - 9 AM", required=True)
    lunch_with_time = fields.Char(string="10 AM - 1 PM", required=True)
    evening_with_time = fields.Char(string="4 PM - 6 PM", required=True)
    dinner_with_time = fields.Char(string="7 PM - 10 PM", required=True)
    comments = fields.Char(string="Comments")
    active = fields.Boolean(default=True, index=True)

    b_cervical = fields.Boolean(string='Cervical')
    b_lumbar = fields.Boolean(string='Lumbar')
    b_knee = fields.Boolean(string='Knee')
    b_shoulder = fields.Boolean(string='Shoulder')
    b_hip = fields.Boolean(string='Hip')
    b_ankle = fields.Boolean(string='Ankle')
    b_tibia = fields.Boolean(string='Tibia')
    b_other = fields.Boolean(string='Other')
    b_if_other = fields.Char(string='If Other')
    body_parts = fields.Char(compute="_compute_body_parts", store=True)

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

    def _lock_enrollment(self, enrollment):
        """Serialize session consumption/reversal for one enrollment."""
        self.env.cr.execute(
            "SELECT id FROM patient_enrollment WHERE id = %s FOR UPDATE",
            [enrollment.id],
        )
        enrollment.invalidate_recordset([
            'used_sessions', 'total_sessions', 'remaining_sessions', 'state'
        ])
        return enrollment

    def _resolve_legacy_enrollment(self):
        """
        Resolve the most likely enrollment for sessions created before
        enrollment_id was added. New sessions always store enrollment_id.

        session_day is used against cumulative paid enrollment capacity, which
        avoids incorrectly decrementing a newer extension when an old session
        is archived.
        """
        self.ensure_one()

        if self.enrollment_id:
            return self.enrollment_id

        enrollments = self.env['patient.enrollment'].sudo().with_context(
            active_test=False
        ).search([
            ('patient_id', '=', self.patient_id.id),
            ('payment_state', '=', 'paid'),
            ('active', '=', True),
            ('total_sessions', '>', 0),
        ], order='enrollment_date asc, id asc')

        if not enrollments:
            return self.env['patient.enrollment']

        if self.session_day and self.session_day > 0:
            cumulative = 0
            for enrollment in enrollments:
                cumulative += enrollment.total_sessions
                if self.session_day <= cumulative:
                    return enrollment

        # Fallback for legacy/inconsistent session_day values: decrement the
        # latest enrollment that currently has a consumed session.
        candidates = enrollments.filtered(lambda e: e.used_sessions > 0)
        return candidates[-1:] if candidates else enrollments[-1:]

    def _adjust_enrollment_used_sessions(self, enrollment, delta):
        """Increment/decrement used_sessions without allowing invalid values."""
        if not enrollment or not delta:
            return

        enrollment = self._lock_enrollment(enrollment.sudo())
        current_used = enrollment.used_sessions or 0

        if delta > 0:
            if current_used >= enrollment.total_sessions:
                raise UserError(
                    "This patient has no remaining sessions in the selected enrollment. "
                    "Kindly contact BM / Admin for further extension."
                )
            new_used = current_used + delta
        else:
            # A repeated archive/delete must never push the count below zero.
            new_used = max(current_used + delta, 0)

        if new_used != current_used:
            enrollment.with_context(session_usage_update=True).sudo().write({
                'used_sessions': new_used
            })

    @api.model
    def create(self, vals):
        # Get the patient either from vals or context
        patient_id = vals.get("patient_id") or self.env.context.get("default_patient_id")
        if not patient_id:
            raise UserError("Patient is required to create a session.")

        patient = self.env["clinic.patient"].browse(patient_id)

        if not vals.get('therapy_clinic_id'):
            vals['therapy_clinic_id'] = patient.clinic_id.id

        # Re-fetch and lock the active enrollment so two users cannot consume
        # the same final remaining session at the same time.
        enrollment = self.env["patient.enrollment"].sudo().search([
            ("id", "=", patient.active_enrollment_id.id),
            ("state", "=", "active"),
            ("active", "=", True),
            ("payment_state", "=", "paid"),
        ], limit=1, order="id asc")

        if not enrollment:
            raise UserError(
                "This patient has no active enrollment. "
                "Kindly contact with BM / Admin for further extension."
            )

        enrollment = self._lock_enrollment(enrollment)
        if enrollment.remaining_sessions <= 0:
            raise UserError(
                "This patient has no active enrollment. "
                "Kindly contact with BM / Admin for further extension."
            )

        # Persist the exact enrollment consumed by this session.
        vals['enrollment_id'] = enrollment.id

        # Create the session first; any later error rolls back the whole transaction.
        session = super().create(vals)

        # Increment used_sessions on the same enrollment.
        self._adjust_enrollment_used_sessions(enrollment, 1)

        # If the patient was previously marked inactive (e.g. missed the
        # activity window), a new valid therapy session reactivates them.
        if patient.patient_status == 'inactive':
            patient.sudo().write({'patient_status': 'active'})

        return session

    def write(self, vals):
        # Capture the enrollment before active is changed. For historical
        # sessions without enrollment_id, resolve the original enrollment as
        # accurately as possible from session_day and enrollment capacity.
        active_change = 'active' in vals
        transitions = []

        if active_change:
            target_active = bool(vals.get('active'))
            for record in self:
                if record.active != target_active:
                    enrollment = record._resolve_legacy_enrollment()
                    transitions.append((record.id, record.active, target_active, enrollment.id if enrollment else False))

        res = super().write(vals)

        if active_change:
            Enrollment = self.env['patient.enrollment'].sudo()
            for record_id, old_active, new_active, enrollment_id in transitions:
                if not enrollment_id:
                    continue

                enrollment = Enrollment.browse(enrollment_id).exists()
                if not enrollment:
                    continue

                if old_active and not new_active:
                    # Session archived/deleted -> give the session back.
                    self.browse(record_id)._adjust_enrollment_used_sessions(enrollment, -1)
                elif not old_active and new_active:
                    # Session restored -> consume it again.
                    self.browse(record_id)._adjust_enrollment_used_sessions(enrollment, 1)

        return res

    @api.onchange('session_type')
    def _onchange_session_type(self):
        if self.session_type == 'self':
            # Ensure the name matches exactly what is in the clinic.therapist table
            self_therapist = self.env['clinic.therapist'].search([('name', '=ilike', 'Self')], limit=1)
            if self_therapist:
                self.therapist_id = self_therapist.id
            else:
                self.therapist_id = False
        else:
            self.therapist_id = False

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        # Soft-delete through write() so used_sessions is reversed exactly once.
        self.write({'active': False})
        return True

    def action_archive(self):
        # 1. Check if the action was triggered from our locked-down dashboard
        if self.env.context.get('block_archive'):
            raise UserError("You cannot archive records directly from the Dashboard view.")

        # 2. Otherwise, allow normal archiving behavior
        return super().action_archive()

    @api.constrains('b_cervical', 'b_lumbar', 'b_knee', 'b_shoulder', 'b_hip', 'b_ankle', 'b_tibia', 'b_other')
    def _check_body_part_selection(self):
        for rec in self:
            if not any([
                rec.b_cervical,
                rec.b_lumbar,
                rec.b_knee,
                rec.b_shoulder,
                rec.b_hip,
                rec.b_ankle,
                rec.b_tibia,
                rec.b_other
            ]):
                raise ValidationError(_("Please select at least one body part."))


    @api.depends('b_cervical', 'b_lumbar', 'b_knee', 'b_shoulder', 'b_hip', 'b_ankle', 'b_tibia', 'b_other', 'b_if_other')
    def _compute_body_parts(self):
        for rec in self:
            selected = []
            if rec.b_cervical:
                selected.append('Cervical')
            if rec.b_lumbar:
                selected.append('Lumbar')
            if rec.b_knee:
                selected.append('Knee')
            if rec.b_shoulder:
                selected.append('Shoulder')
            if rec.b_hip:
                selected.append('Hip')
            if rec.b_ankle:
                selected.append('Ankle')
            if rec.b_tibia:
                selected.append('Tibia')
            if rec.b_other:
                selected.append(f"Other ({rec.b_if_other})" if rec.b_if_other else "Other")

            rec.body_parts = ", ".join(selected)

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))