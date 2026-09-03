from odoo import api, models, fields, _
from odoo.exceptions import ValidationError
from datetime import datetime, time
import pytz


class PatientSession(models.Model):
    _inherit = 'patient.session'

    @api.constrains('clinic_id', 'therapist_id', 'session_type')
    def _check_therapist_allowed_branch(self):
        for rec in self:
            if rec.session_type == 'self':
                continue
            if not rec.therapist_id or not rec.clinic_id:
                continue

            if rec.clinic_id not in rec.therapist_id.allowed_branch_ids:
                raise ValidationError(
                    _(
                        "%(therapist)s is not allowed "
                        "for %(clinic)s."
                    )
                    % {
                        'therapist': rec.therapist_id.name,
                        'clinic': rec.clinic_id.name,
                    }
                )

    def _sync_matrix_completion(self):
        """Automatically marks corresponding matrix slot as completed when doctor creates session."""
        Appointment = self.env['clinic.schedule.appointment'].sudo()
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')

        for rec in self:
            if not rec.patient_id or not rec.session_date:
                continue

            # Compute exact local UTC boundaries for the session date
            start_local = local_tz.localize(datetime.combine(rec.session_date, time.min))
            end_local = local_tz.localize(datetime.combine(rec.session_date, time.max))
            start_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)
            end_utc = end_local.astimezone(pytz.utc).replace(tzinfo=None)

            # Find matching scheduled appointment for this patient on this day
            domain = [
                ('patient_id', '=', rec.patient_id.id),
                ('slot_type', '=', 'patient'),
                ('start_datetime', '>=', start_utc),
                ('start_datetime', '<=', end_utc),
                ('attendance_state', '!=', 'no_show')
            ]

            # Target pending/scheduled appointments first, or fallback to first session of the day
            apps = Appointment.search(domain, order='attendance_state desc, start_datetime asc')
            target_app = apps.filtered(lambda a: a.attendance_state != 'completed')[:1] or apps[:1]

            if target_app:
                mismatch = bool(
                    rec.therapist_id and target_app.therapist_id and rec.therapist_id.id != target_app.therapist_id.id)
                vals = {
                    'attendance_state': 'completed',
                    'actual_therapist_id': rec.therapist_id.id if rec.therapist_id else False,
                    'is_therapist_mismatch': mismatch,
                    'manual_completion_user_id': False  # NEW: Clear manual flag since doctor logged it natively!
                }

                # If slot was unassigned on the board, populate it with the doctor's therapist
                # If slot was unassigned on the board, populate it with the doctor's therapist
                if not target_app.therapist_id and rec.therapist_id:
                    vals['therapist_id'] = rec.therapist_id.id

                # NEW: Pass the bypass token so the Matrix accepts backend syncs
                target_app.with_context(bypass_matrix_lock=True).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_matrix_completion()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'therapist_id' in vals or 'session_date' in vals or 'patient_id' in vals:
            self._sync_matrix_completion()
        return res