import base64
import io
import csv
import requests
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta, datetime, time
import pytz

_logger = logging.getLogger(__name__)

class ClinicScheduleState(models.Model):
    _name = 'clinic.schedule.state'
    _description = 'User Schedule Memory State'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade', index=True)
    last_region_id = fields.Integer(string='Last Region ID', default=0)
    last_clinic_id = fields.Integer(string='Last Clinic ID', default=0)

    _sql_constraints = [
        ('unique_user_state', 'unique(user_id)', 'A user can only have one memory state record!')
    ]

class ClinicScheduleLock(models.Model):
    _name = 'clinic.schedule.lock'
    _description = 'Clinic Schedule Date Lock'

    clinic_id = fields.Many2one('clinic.clinic', string='Clinic Location', required=True, ondelete='cascade', index=True)
    target_date = fields.Date(string='Target Date', required=True, index=True)
    is_locked = fields.Boolean(string='Is Locked', default=True)
    locked_by_id = fields.Many2one('res.users', string='Locked By', default=lambda self: self.env.user)
    locked_datetime = fields.Datetime(string='Locked At', default=fields.Datetime.now)

    _sql_constraints = [
        ('uniq_clinic_date_lock', 'unique(clinic_id, target_date)', 'A lock record already exists for this clinic and date!')
    ]

    @api.model
    def action_toggle_matrix_lock(self, clinic_id, target_date):
        """Locks or unlocks the matrix for a specific clinic and date. Restricted strictly to Managers."""
        if not self.env.user.has_group('clinic_schedule.group_clinic_schedule_manager'):
            raise ValidationError(_("Access Denied: Only Managers can lock or unlock the schedule matrix."))

        if not clinic_id or not target_date:
            return {'status': 'warning', 'message': _('Missing Clinic or Target Date.')}

        clinic_id = int(clinic_id)
        Lock = self.env['clinic.schedule.lock']
        lock_rec = Lock.search([('clinic_id', '=', clinic_id), ('target_date', '=', target_date)], limit=1)

        if lock_rec and lock_rec.is_locked:
            lock_rec.write({'is_locked': False})
            return {'status': 'success', 'is_locked': False,
                    'message': _('Schedule for %s has been unlocked.') % target_date}
        elif lock_rec:
            lock_rec.write(
                {'is_locked': True, 'locked_by_id': self.env.user.id, 'locked_datetime': fields.Datetime.now()})
            return {'status': 'success', 'is_locked': True,
                    'message': _('Schedule for %s has been locked.') % target_date}
        else:
            Lock.create({
                'clinic_id': clinic_id,
                'target_date': target_date,
                'is_locked': True,
                'locked_by_id': self.env.user.id,
                'locked_datetime': fields.Datetime.now()
            })
            return {'status': 'success', 'is_locked': True,
                    'message': _('Schedule for %s has been locked.') % target_date}

class ClinicManualCompleteWizard(models.TransientModel):
    _name = 'clinic.manual.complete.wizard'
    _description = 'Confirm Manual Completion'

    appointment_id = fields.Many2one('clinic.schedule.appointment', required=True)

    def action_confirm(self):
        self.ensure_one()
        # Pass force=True to bypass the warning
        self.appointment_id.action_mark_completed(force=True)
        return {'type': 'ir.actions.act_window_close'}


class ClinicClinic(models.Model):
    _inherit = 'clinic.clinic'
    region_id = fields.Many2one('clinic.region', string='Operating Region')



class ClinicTherapist(models.Model):
    _name = 'clinic.therapist'
    _inherit = ['clinic.therapist', 'mail.thread', 'mail.activity.mixin']

    # --- MODERN HR FIELDS ---
    image_1920 = fields.Image("Avatar", max_width=1920, max_height=1920)
    image_128 = fields.Image("Avatar 128", related="image_1920", max_width=128, max_height=128, store=True)
    state = fields.Selection([
        ('onboarding', 'Onboarding'),
        ('active', 'Active'),
        ('offboarded', 'Offboarded')
    ], string='Employment Status', default='onboarding', tracking=True)

    active = fields.Boolean(string="Active", default=True, tracking=True)
    vendor_id = fields.Char(string='Vendor ID', required=True, copy=False, tracking=True)
    contact_number = fields.Char(string="Phone Number", tracking=True, required=True)
    is_buffer = fields.Boolean(string="Is Buffer / Emergency Row", default=False, tracking=True,
                               help="Check this to permanently pin this row to the top of the clinic matrix for walk-ins.")
    gender = fields.Selection([('m', 'Male'), ('f', 'Female'), ('o', 'Other')], string="Gender", tracking=True, required=True)
    transport_type = fields.Selection([
        ('two_wheeler', 'Two-Wheeler'), ('four_wheeler', 'Four-Wheeler'),
        ('public', 'Public Transport'), ('company', 'Company Vehicle')
    ], string="Transport Type", tracking=True)
    designation = fields.Selection([
        ('fixed', 'Fixed Therapist'),
        ('floater', 'Clinic Floater'),
        ('hv', 'HV Floater')
    ], string="Deployment Type", default='fixed', required=True, tracking=True)
    allowed_branch_ids = fields.Many2many('clinic.clinic', string="Allowed Branches", tracking=True)
    base_branch_id = fields.Many2one('clinic.clinic', string="Base Branch", tracking=True,
                                     help="The primary home clinic for this therapist.")

    # --- FINANCIAL FIELDS ---
    hourly_rate = fields.Float(string="Hourly Pay Rate", tracking=True, help="Standard hourly compensation.")
    bank_name = fields.Char(string="Bank Name", tracking=True)
    bank_account_name = fields.Char(string="Account Holder Name", tracking=True)
    bank_account_number = fields.Char(string="Account Number", tracking=True)
    bank_ifsc_code = fields.Char(string="IFSC Code", tracking=True)

    # --- FLOATER REQUEST (PLACEHOLDER) FIELDS ---
    is_floater_request = fields.Boolean(string="Is Floater Request Placeholder", default=False, tracking=True)
    request_clinic_id = fields.Many2one('clinic.clinic', string="Requested For Clinic", tracking=True)
    request_date = fields.Date(string="Requested Date", tracking=True)
    request_state = fields.Selection([
        ('pending', 'Pending Approval'),
        ('approved', 'Approved & Substituted'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled')
    ], string="Request State", tracking=True)

    _sql_constraints = [
        ('unique_vendor_id', 'unique(vendor_id)', 'Vendor ID must be unique across all therapists!')
    ]

    # 1. ADD THESE FIELDS inside class ClinicTherapist(models.Model):
    leaving_date = fields.Date(string="Date of Leaving", tracking=True)
    leaving_reason = fields.Text(string="Reason for Leaving", tracking=True)
    aadhaar_document = fields.Binary(string="Aadhaar Card", attachment=True)
    aadhaar_filename = fields.Char(string="Aadhaar Filename")  # NEW

    pan_document = fields.Binary(string="PAN Card", attachment=True)
    pan_filename = fields.Char(string="PAN Filename")  # NEW

    def action_activate(self):
        """Completes Onboarding"""
        for rec in self:
            rec.state = 'active'
            rec.active = True

    def action_offboard(self):
        """Triggers the Offboarding Wizard"""
        self.ensure_one()
        return {
            'name': _('Offboard Therapist'),
            'type': 'ir.actions.act_window',
            'res_model': 'clinic.therapist.offboard.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_therapist_id': self.id}
        }

    def action_reonboard(self):
        """Restores an offboarded therapist back into the onboarding queue"""
        for rec in self:
            rec.state = 'onboarding'
            rec.active = True

    def action_toggle_buffer(self):
        for rec in self:
            rec.is_buffer = not rec.is_buffer
            state_str = "ENABLED (Pinned to Top)" if rec.is_buffer else "DISABLED"
            rec.message_post(body=_("<b>Audit Log:</b> Walk-in Buffer state changed to <b>%s</b> by <b>%s</b>.") % (
                state_str, self.env.user.name
            ))
        return True

    @api.model
    def _cron_reset_daily_floaters(self):
        """
        Runs nightly. Strips the temporary branch allocations from all pan-India
        floaters so they can be freshly requested and routed the next day.
        """
        floaters = self.search([
            ('designation', 'in', ['floater', 'hv']),
            ('allowed_branch_ids', '!=', False)
        ])
        for floater in floaters:
            # (5, 0, 0) is the ORM command to clear the Many2many relation entirely
            floater.write({'allowed_branch_ids': [(5, 0, 0)]})
        _logger.info(f"Nightly Matrix Reset: Cleared branch assignments for {len(floaters)} floaters.")

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True

class ClinicTherapistDailyState(models.Model):
    _name = 'clinic.therapist.daily.state'
    _description = 'Therapist Daily Attendance Overlay'

    therapist_id = fields.Many2one('clinic.therapist', required=True, ondelete='cascade')
    target_date = fields.Date(required=True, index=True)
    action_type = fields.Selection([
        ('no_show', 'No Show'), ('wo', 'Week Off'),
        ('leave', 'Leave'), ('late', 'Late')
    ], required=True)
    expected_hour = fields.Integer(string="Expected Arrival Hour", default=0)

    _sql_constraints = [
        ('unique_daily_state', 'unique(therapist_id, target_date)',
         'A therapist can only have one state overlay per day!')
    ]


class ClinicScheduleAppointment(models.Model):
    _name = 'clinic.schedule.appointment'
    _description = 'Clinic Appointment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'start_datetime'

    name = fields.Char(string='Appointment ID', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic Location', required=True, tracking=True)
    therapist_id = fields.Many2one('clinic.therapist', string='Therapist Name', required=False, tracking=True)
    attendance_state = fields.Selection([
        ('scheduled', 'Scheduled'), ('in_progress', 'In Progress'),
        ('completed', 'Completed'), ('no_show', 'No-Show'),
    ], string="Session Status", default='scheduled', required=True, tracking=True, index=True)
    slot_type = fields.Selection([
        ('patient', 'Patient Session'), ('lunch', 'Lunch Break'),
        ('wo', 'Week Off'), ('leave', 'Leave'),
        ('training', 'Training'), ('blocked', 'Blocked')
    ], string="Slot Type", default='patient', required=True, tracking=True, index=True)
    visit_type = fields.Selection([
        ('clinic', 'Clinic Visit'),
        ('home', 'Home Visit'),
        ('self', 'Self Therapy')
    ], string="Visit Type", default='clinic', tracking=True)

    notification_status = fields.Selection([
        ('pending', 'Pending'),
        ('queued', 'Queued'),
        ('wa_delivered', 'WA Delivered'),
        ('sms_delivered', 'SMS Delivered'),
        ('failed', 'Failed')
    ], string='Notification Status', default='pending', tracking=True)
    patient_id = fields.Many2one('clinic.patient', string='Patient Name', tracking=True)
    start_datetime = fields.Datetime(string='Start Time', required=True, default=fields.Datetime.now,
                                     tracking=True, index=True)
    end_datetime = fields.Datetime(string='End Time', compute='_compute_end_datetime', store=True, readonly=False,
                                   tracking=True)
    allowed_patient_ids = fields.Many2many('clinic.patient', compute='_compute_allowed_patient_ids')

    is_live_in_progress = fields.Boolean(compute='_compute_live_status', store=False)

    CRITICAL_SCHEDULE_FIELDS = {
        'start_datetime', 'end_datetime', 'therapist_id',
        'clinic_id', 'slot_type', 'visit_type'
    }

    def write(self, vals):
        sys_bypass = self.env.su or self.env.context.get('bypass_matrix_lock')
        is_manager = self.env.user.has_group('clinic_schedule.group_clinic_schedule_manager')
        business_fields = self.CRITICAL_SCHEDULE_FIELDS | {'patient_id', 'attendance_state'}

        # 1. HARD LOCK ENFORCEMENT
        if not sys_bypass and any(f in vals for f in business_fields):
            for rec in self:
                # A. Date Lock - Applies to EVERYONE (including Managers)
                if rec._is_date_locked(rec.clinic_id.id, rec.start_datetime):
                    raise ValidationError(
                        _("Schedule Locked: The schedule for %s on this date is locked. Please unlock the matrix first.") % rec.clinic_id.name)

                # Check if moving appointment to a new date/clinic that is locked
                target_clinic = vals.get('clinic_id', rec.clinic_id.id)
                target_dt = vals.get('start_datetime', rec.start_datetime)
                if (vals.get('clinic_id') or vals.get('start_datetime')) and rec._is_date_locked(target_clinic,
                                                                                                 target_dt):
                    raise ValidationError(_("Schedule Locked: Target clinic date is locked. Please unlock it first."))

                # B. 'Completed' Status Lock - Applies only to non-managers
                if not is_manager and rec.attendance_state == 'completed':
                    raise ValidationError(
                        _("Record Locked: This session is 'Completed'. Only Managers can modify completed sessions."))

        # 2. AUDIT LOG GENERATION (Captured before values change)
        audit_map = {}
        for rec in self:
            entries = []
            if 'therapist_id' in vals:
                old_t = rec.therapist_id.name if rec.therapist_id else 'Unassigned'
                new_t_obj = self.env['clinic.therapist'].browse(vals['therapist_id']) if vals.get(
                    'therapist_id') else False
                new_t = new_t_obj.name if new_t_obj else 'Unassigned'
                if old_t != new_t:
                    entries.append(_("Therapist changed: <b>%s</b> &rarr; <b>%s</b>") % (old_t, new_t))
            if 'attendance_state' in vals:
                old_st = dict(self._fields['attendance_state'].selection).get(rec.attendance_state,
                                                                              rec.attendance_state)
                new_st = dict(self._fields['attendance_state'].selection).get(vals['attendance_state'],
                                                                              vals['attendance_state'])
                if old_st != new_st:
                    entries.append(_("Session Status changed: <b>%s</b> &rarr; <b>%s</b>") % (old_st, new_st))
            if 'start_datetime' in vals:
                old_time = fields.Datetime.context_timestamp(self, rec.start_datetime).strftime(
                    '%d %b %Y, %I:%M %p') if rec.start_datetime else 'None'
                new_dt_obj = fields.Datetime.from_string(vals['start_datetime'])
                new_time = fields.Datetime.context_timestamp(self, new_dt_obj).strftime(
                    '%d %b %Y, %I:%M %p') if new_dt_obj else 'None'
                entries.append(_("Schedule Time changed: <b>%s</b> &rarr; <b>%s</b>") % (old_time, new_time))
            if 'clinic_id' in vals:
                old_c = rec.clinic_id.name if rec.clinic_id else 'None'
                new_c_obj = self.env['clinic.clinic'].browse(vals['clinic_id']) if vals.get('clinic_id') else False
                new_c = new_c_obj.name if new_c_obj else 'None'
                entries.append(_("Clinic Branch changed: <b>%s</b> &rarr; <b>%s</b>") % (old_c, new_c))
            if entries:
                audit_map[rec.id] = entries

        # 3. SAVE CHANGES
        res = super().write(vals)

        # 4. POST AUDIT ENTRIES TO CHATTER
        for rec_id, entries in audit_map.items():
            self.browse(rec_id).message_post(body=_("<b>Audit Log (%s):</b><br/>%s") % (
                self.env.user.name, "<br/>".join(entries)
            ))

        # 5. NOTIFICATION RESET TRACKING
        schedule_modified = any(field in vals for field in self.CRITICAL_SCHEDULE_FIELDS)
        if schedule_modified and not self.env.context.get('bypass_notification_reset'):
            for rec in self:
                if rec.slot_type == 'patient' and rec.notification_status in ['queued', 'wa_delivered',
                                                                              'sms_delivered']:
                    rec.with_context(bypass_notification_reset=True, bypass_matrix_lock=True).write(
                        {'notification_status': 'pending'})
                    rec.message_post(body=_("<b>Schedule Modified:</b> WhatsApp notification status reset to Pending."))

        return res

    # Locate class ClinicScheduleAppointment(models.Model):
    actual_therapist_id = fields.Many2one('clinic.therapist', string="Actually Performed By", tracking=True)
    is_therapist_mismatch = fields.Boolean(string="Therapist Mismatch", default=False, tracking=True)
    manual_completion_user_id = fields.Many2one('res.users', string="Manually Completed By", tracking=True)

    @api.depends('start_datetime', 'end_datetime', 'attendance_state')
    def _compute_live_status(self):
        now_utc = datetime.utcnow()
        for rec in self:
            if rec.attendance_state == 'scheduled' and rec.start_datetime and rec.end_datetime:
                rec.is_live_in_progress = (rec.start_datetime <= now_utc <= rec.end_datetime)
            else:
                rec.is_live_in_progress = False

    @api.model
    def action_carry_forward_schedule(self, clinic_id, target_date):
        if not clinic_id or not target_date:
            return {'status': 'warning', 'message': 'Missing Clinic or Date.'}

        clinic_id = int(clinic_id)
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')

        # Exact IST Boundaries
        target_dt = fields.Date.from_string(target_date)
        yesterday_dt = target_dt - timedelta(days=1)

        y_start = local_tz.localize(datetime.combine(yesterday_dt, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        y_end = local_tz.localize(datetime.combine(yesterday_dt, time.max)).astimezone(pytz.utc).replace(tzinfo=None)

        t_start = local_tz.localize(datetime.combine(target_dt, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        t_end = local_tz.localize(datetime.combine(target_dt, time.max)).astimezone(pytz.utc).replace(tzinfo=None)

        # 1. Fetch yesterday's COMPLETED sessions
        yesterday_sessions = self.search([
            ('clinic_id', '=', clinic_id),
            ('slot_type', '=', 'patient'),
            ('attendance_state', '=', 'completed'),
            ('start_datetime', '>=', y_start),
            ('start_datetime', '<=', y_end)
        ])

        # 2. Fetch today's booked patients (Anti-duplication Check)
        today_sessions = self.search([
            ('clinic_id', '=', clinic_id),
            ('slot_type', '=', 'patient'),
            ('start_datetime', '>=', t_start),
            ('start_datetime', '<=', t_end)
        ])
        today_patient_ids = today_sessions.mapped('patient_id.id')

        # BATCH QUERY: Pre-fetch all of today's booked staff sessions to check overlaps locally
        target_therapists = yesterday_sessions.mapped('therapist_id.id')
        today_staff_apps = self.search([
            ('therapist_id', 'in', target_therapists),
            ('start_datetime', '>=', t_start),
            ('start_datetime', '<=', t_end),
            ('attendance_state', '!=', 'no_show')
        ])

        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
        absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]

        carried_count, unassigned_count = 0, 0
        new_apps_vals = []

        for old_app in yesterday_sessions:
            if not old_app.patient_id or old_app.patient_id.id in today_patient_ids: continue
            if old_app.patient_id.remaining_sessions <= 0: continue

            new_start = old_app.start_datetime + timedelta(days=1)
            new_end = old_app.end_datetime + timedelta(days=1)
            target_therapist_id = old_app.therapist_id.id if old_app.therapist_id else False
            is_unassigned = False

            if target_therapist_id:
                if old_app.therapist_id.designation in ['floater', 'hv'] or target_therapist_id in absent_staff_ids:
                    target_therapist_id = False
                    is_unassigned = True
                else:
                    # LOCAL MEMORY CHECK: No database query inside the loop
                    overlap = any(
                        a.start_datetime < new_end and a.end_datetime > new_start
                        for a in today_staff_apps if a.therapist_id.id == target_therapist_id
                    )
                    if overlap:
                        target_therapist_id = False
                        is_unassigned = True

            new_apps_vals.append({
                'clinic_id': clinic_id,
                'therapist_id': target_therapist_id,
                'patient_id': old_app.patient_id.id,
                'slot_type': 'patient',
                'visit_type': old_app.visit_type,
                'attendance_state': 'scheduled',
                'start_datetime': new_start,
                'end_datetime': new_end,
            })
            carried_count += 1
            if is_unassigned: unassigned_count += 1

        if new_apps_vals:
            self.create(new_apps_vals)

        if carried_count == 0:
            return {'status': 'info',
                    'message': 'No eligible completed sessions found to carry forward from yesterday.'}
        return {'status': 'success',
                'message': f'Carry Forward: {carried_count} patients moved. {unassigned_count} dropped to UNASSIGNED (Therapist absent/busy).'}

    is_future_session = fields.Boolean(compute='_compute_is_future_session', store=False)

    @api.depends('start_datetime')
    def _compute_is_future_session(self):
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        today_local = datetime.now(local_tz).date()
        for rec in self:
            if rec.start_datetime:
                rec_local_date = pytz.utc.localize(rec.start_datetime).astimezone(local_tz).date()
                rec.is_future_session = rec_local_date > today_local
            else:
                rec.is_future_session = False

    # @api.model
    # def _cron_generate_daily_payouts(self):
    #     """
    #     Runs at 11:55 PM daily. Calculates Time-Fenced Incentives and OT,
    #     and routes the financial voucher to the therapist's last clinic of the day.
    #     """
    #     local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
    #     target_date = datetime.now(local_tz).date()
    #
    #     # 1. Calculate strict local time boundaries
    #     start_of_day_local = local_tz.localize(datetime.combine(target_date, time.min))
    #     end_of_day_local = local_tz.localize(datetime.combine(target_date, time.max))
    #
    #     # 2. Convert boundaries safely to UTC for database querying
    #     start_day_utc = start_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
    #     end_day_utc = end_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
    #
    #     # Get all valid worked appointments for the day using UTC boundaries
    #     daily_apps = self.search([
    #         ('start_datetime', '>=', start_day_utc),
    #         ('end_datetime', '<=', end_day_utc),
    #         ('attendance_state', 'in', ['completed', 'in_progress']),
    #         ('therapist_id', '!=', False)
    #     ], order='start_datetime asc')
    #
    #     # Group chronologically by therapist
    #     therapist_map = {}
    #     for app in daily_apps:
    #         t_id = app.therapist_id
    #         if t_id not in therapist_map:
    #             therapist_map[t_id] = []
    #         therapist_map[t_id].append(app)
    #
    #     Disbursement = self.env['operational.fund.disbursement'].sudo()
    #     vouchers_to_create = []
    #
    #     for therapist, apps in therapist_map.items():
    #         cumulative_hours = 0.0
    #         therapies_in_standard_time = 0
    #
    #         # 1. Chronological Timeline Analysis
    #         for app in apps:
    #             duration_hours = (app.end_datetime - app.start_datetime).total_seconds() / 3600.0
    #
    #             # Check if this appointment falls entirely or partially within the 9-hour window
    #             if cumulative_hours < 9.0:
    #                 if app.slot_type == 'patient' and app.attendance_state == 'completed':
    #                     therapies_in_standard_time += 1
    #
    #             cumulative_hours += duration_hours

            # 2. Identify the Last Clinic
            # last_clinic = apps[-1].clinic_id

            # 3. Calculate Incentive (Strictly inside the 9-hour window)
            # if therapies_in_standard_time > 6:
            #     incentive_amount = (therapies_in_standard_time - 6) * 120.0
            #     vouchers_to_create.append({
            #         'clinic_id': last_clinic.id,
            #         'date': target_date,
            #         'expense_category': 'incentive',
            #         'therapist_role': therapist.designation if therapist.designation in ['home', 'fixed',
            #                                                                              'floater'] else 'fixed',
            #         'therapist_ref_id': therapist.id,
            #         'amount': incentive_amount,
            #         'is_system_generated': True,
            #         'description': f"Automated Matrix Payout: Completed {therapies_in_standard_time} therapies within standard 9-hour shift. Base: 6.",
            #         'state': 'waiting'  # Sends it to Custodian Dashboard
            #     })

            # 4. Calculate Overtime (Strictly outside the 9-hour window)
            # if cumulative_hours > 9.0:
            #     ot_hours = cumulative_hours - 9.0
            #     ot_amount = round(ot_hours * 120.0, 2)
            #     vouchers_to_create.append({
            #         'clinic_id': last_clinic.id,
            #         'date': target_date,
            #         'expense_category': 'overtime',
            #         'therapist_role': therapist.designation if therapist.designation in ['home', 'fixed',
            #                                                                              'floater'] else 'fixed',
            #         'therapist_ref_id': therapist.id,
            #         'amount': ot_amount,
            #         'is_system_generated': True,
            #         'description': f"Automated Matrix Payout: {round(ot_hours, 2)} hours of tracked Overtime.",
            #         'state': 'waiting'
            #     })

        # Inject all validated vouchers into the operational fund
        # if vouchers_to_create:
        #     Disbursement.create(vouchers_to_create)
        #     _logger.info(
        #         f"System Matrix generated {len(vouchers_to_create)} automated payout vouchers for {target_date}.")


    def _is_date_locked(self, clinic_id, dt):
        """Checks whether a specific clinic and date is hard-locked in clinic.schedule.lock."""
        if not clinic_id or not dt:
            return False
        if isinstance(dt, str):
            dt = fields.Datetime.from_string(dt)
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        target_date = pytz.utc.localize(dt).astimezone(local_tz).date() if dt.tzinfo is None else dt.astimezone(
            local_tz).date()

        return bool(self.env['clinic.schedule.lock'].sudo().search_count([
            ('clinic_id', '=', int(clinic_id)),
            ('target_date', '=', target_date),
            ('is_locked', '=', True),
        ]))

    @api.model
    def action_reject_floater(self, placeholder_id):
        """ Rejects the request, deletes the placeholder, and moves patients to UNASSIGNED """
        placeholder = self.env['clinic.therapist'].browse(int(placeholder_id))
        if not placeholder.exists() or not placeholder.is_floater_request:
            return False

        # Move any booked appointments to the UNASSIGNED pool
        apps = self.search([('therapist_id', '=', placeholder.id)])
        apps.write({'therapist_id': False})
        for app in apps:
            app.message_post(body=_(
                "<b>System Auto-Unassigned:</b> Floater request was rejected by HO. Session moved to unassigned pool."))

        # Archive placeholder
        placeholder.write({
            'active': False,
            'request_state': 'rejected'
        })
        return True

    @api.model
    def action_cancel_floater_request(self, placeholder_id):
        """ Allows Admins to cancel their own pending requests and dump booked slots. """
        placeholder = self.env['clinic.therapist'].sudo().browse(int(placeholder_id))
        if not placeholder.exists() or not placeholder.is_floater_request:
            return False

        # Dump patients to UNASSIGNED
        apps = self.search([('therapist_id', '=', placeholder.id)])
        unassigned_count = len(apps)
        apps.write({'therapist_id': False})
        for app in apps:
            app.message_post(body=_("<b>System Auto-Unassigned:</b> Clinic Admin cancelled the floater request."))

        # Clean up pending Manager notification activities
        self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'clinic.therapist'),
            ('res_id', '=', placeholder.id)
        ]).unlink()

        # Archive the placeholder
        placeholder.write({
            'active': False,
            'request_state': 'cancelled'
        })
        return {'status': 'success',
                'message': f'Request cancelled. {unassigned_count} patients dropped to UNASSIGNED.'}

    @api.model
    def action_substitute_floater(self, placeholder_id, real_therapist_id):
        """ Accepts the request, links the branch, moves patients, and cleans up notifications. """
        placeholder = self.env['clinic.therapist'].browse(int(placeholder_id))
        real_therapist = self.env['clinic.therapist'].browse(int(real_therapist_id))

        if not placeholder.exists() or not real_therapist.exists():
            raise ValidationError(_("Invalid therapist selection."))

        # 1. Grant the real floater temporary access to the requesting clinic's matrix
        target_clinic_id = placeholder.request_clinic_id.id
        if target_clinic_id:
            real_therapist.write({'allowed_branch_ids': [(4, target_clinic_id)]})

        # 2. Reassign any patients already safely booked on the placeholder
        apps = self.search([('therapist_id', '=', placeholder.id)])
        if apps:
            apps.write({'therapist_id': real_therapist.id})
            for app in apps:
                app.message_post(body=_(
                    "<b>Audit Log:</b> Session successfully substituted from Requested Placeholder to Real Floater: %s") % real_therapist.name)

        # 3. Clean up Manager To-Do Activities safely (Odoo 17 Fix)
        self.env['mail.activity'].search([
            ('res_model', '=', 'clinic.therapist'),
            ('res_id', '=', placeholder.id)
        ]).unlink()

        # 4. Approve and archive the placeholder
        placeholder.write({
            'active': False,
            'request_state': 'approved'
        })
        return True

    @api.model
    def action_remove_therapist_from_board(self, therapist_id, clinic_id, target_date):
        """Removes the branch from the therapist and unassigns all their local patients from that day forward."""
        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)

        # FIX: Removed end_day boundary to unassign for target date AND all future dates
        target_date_obj = fields.Date.from_string(target_date)
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        start_local = local_tz.localize(datetime.combine(target_date_obj, time.min))
        start_day_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)

        apps = self.search([
            ('therapist_id', '=', int(therapist_id)),
            ('clinic_id', '=', int(clinic_id)),
            ('start_datetime', '>=', start_day_utc),
            ('attendance_state', 'not in', ['completed', 'no_show'])
        ])
        unassigned_count = len(apps)
        apps.write({'therapist_id': False})

        for app in apps:
            app.message_post(body="System Auto-Unassigned: Therapist was removed from the matrix board.")

        therapist = self.env['clinic.therapist'].browse(int(therapist_id))
        therapist.write({'allowed_branch_ids': [(3, int(clinic_id))]})

        return {'status': 'success', 'message': f'Removed therapist and routed {unassigned_count} slots to UNASSIGNED.'}

    @api.model
    def action_mass_reassign_sessions(self, source_therapist_id, target_therapist_id, clinic_id, target_date):
        """Mass shifts all sessions from one therapist (or UNASSIGNED) to a target therapist."""
        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
        end_day = datetime.combine(fields.Date.from_string(target_date), time.max)
        start_day_utc = start_day.astimezone(pytz.utc).replace(tzinfo=None)
        end_day_utc = end_day.astimezone(pytz.utc).replace(tzinfo=None)

        domain = [
            ('clinic_id', '=', int(clinic_id)),
            ('start_datetime', '>=', start_day_utc),
            ('start_datetime', '<=', end_day_utc),
            ('slot_type', '=', 'patient'),
            ('attendance_state', 'not in', ['completed', 'no_show'])
        ]

        if int(source_therapist_id) == 0:
            domain.append(('therapist_id', '=', False))
        else:
            domain.append(('therapist_id', '=', int(source_therapist_id)))

        apps = self.search(domain)
        target_t = self.env['clinic.therapist'].browse(int(target_therapist_id))

        count = len(apps)
        apps.write({'therapist_id': target_t.id})

        for app in apps:
            app.message_post(body=f"System Mass-Reassigned: Moved session to {target_t.name}.")

        return {'status': 'success', 'message': f'Successfully moved {count} sessions to {target_t.name}.'}

    # @api.model
    # def action_substitute_floater(self, placeholder_id, real_therapist_id):
    #     """ Accepts the request, moves patients to the real floater, and hides the placeholder """
    #     placeholder = self.env['clinic.therapist'].browse(int(placeholder_id))
    #     real_therapist = self.env['clinic.therapist'].browse(int(real_therapist_id))
    #
    #     if not placeholder.exists() or not real_therapist.exists():
    #         raise ValidationError(_("Invalid therapist selection."))
    #
    #     # Reassign all patients securely to the actual floater
    #     apps = self.search([('therapist_id', '=', placeholder.id)])
    #     apps.write({'therapist_id': real_therapist.id})
    #     for app in apps:
    #         app.message_post(body=_(
    #             "<b>Audit Log:</b> Session successfully substituted from Requested Placeholder to Real Floater: %s") % real_therapist.name)
    #
    #     # Approve and archive the placeholder
    #     placeholder.write({
    #         'active': False,
    #         'request_state': 'approved'
    #     })
    #     return True
    # @api.model
    # def action_check_floater_eligibility(self, clinic_id, target_date):
    #     if not clinic_id or not target_date:
    #         return False
    #
    #     clinic_id = int(clinic_id)
    #     target_date_obj = fields.Date.from_string(target_date)
    #
    #     local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
    #     start_of_day_local = local_tz.localize(datetime.combine(target_date_obj, time.min))
    #     end_of_day_local = local_tz.localize(datetime.combine(target_date_obj, time.max))
    #     start_day_utc = start_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
    #     end_day_utc = end_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
    #
    #     daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
    #     absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]
    #
    #     # 1. DEFINE working_therapists FIRST
    #     working_therapists = self.env['clinic.therapist'].search([
    #         ('active', '=', True),
    #         ('is_buffer', '=', False),
    #         ('is_floater_request', '=', False),
    #         ('allowed_branch_ids', 'in', clinic_id)
    #     ]).filtered(lambda t: t.id not in absent_staff_ids)
    #
    #     # 2. Fetch today's appointments
    #     today_apps = self.search([
    #         ('clinic_id', '=', clinic_id),
    #         ('slot_type', '=', 'patient'),
    #         ('attendance_state', '!=', 'no_show'),
    #         ('start_datetime', '>=', start_day_utc),
    #         ('start_datetime', '<=', end_day_utc),
    #         ('therapist_id', 'in', working_therapists.ids)
    #     ])
    #
    #     # 3. Build therapist_counts
    #     therapist_counts = {t.id: 0 for t in working_therapists}
    #     for app in today_apps:
    #         therapist_counts[app.therapist_id.id] += 1
    #
    #     # 4. NOW calculate gender specific utilization
    #     male_working = working_therapists.filtered(lambda t: t.gender == 'm')
    #     female_working = working_therapists.filtered(lambda t: t.gender == 'f')
    #
    #     male_underutilized = any(therapist_counts[t.id] < 6 for t in male_working) if male_working else False
    #     female_underutilized = any(therapist_counts[t.id] < 6 for t in female_working) if female_working else False
    #
    #     underutilized_exists = False
    #     for t in working_therapists:
    #         if therapist_counts[t.id] < 6:
    #             underutilized_exists = True
    #             break
    #
    #     # Calculate if the branch completely lacks a specific gender
    #     missing_genders = []
    #     if not any(t.gender == 'm' for t in working_therapists):
    #         missing_genders.append('m')
    #     if not any(t.gender == 'f' for t in working_therapists):
    #         missing_genders.append('f')
    #
    #     # Block ONLY if they have underutilized staff AND both genders are already present
    #     if underutilized_exists and not missing_genders:
    #         raise ValidationError(_(
    #             "Capacity threshold not met: All working therapists must have at least 6 assigned therapies before requesting a floater."
    #         ))
    #
    #     if male_underutilized and female_underutilized:
    #         raise ValidationError(_(
    #             "Capacity threshold not met: Working therapists must have at least 6 assigned therapies before requesting additional floaters."
    #         ))
    #
    #     return {
    #         'name': _('Request Floater Therapist'),
    #         'type': 'ir.actions.act_window',
    #         'res_model': 'clinic.floater.request.wizard',
    #         'view_mode': 'form',
    #         'views': [[False, 'form']],
    #         'target': 'new',
    #         'context': {
    #             'default_clinic_id': clinic_id,
    #             'default_target_date': target_date,
    #             'male_underutilized': male_underutilized,
    #             'female_underutilized': female_underutilized,
    #             'missing_genders': missing_genders
    #         }
    #     }

    @api.model
    def action_check_floater_eligibility(self, clinic_id, target_date):
        if not clinic_id or not target_date:
            return False

        return {
            'name': _('Request Floater Therapist'),
            'type': 'ir.actions.act_window',
            'res_model': 'clinic.floater.request.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'default_clinic_id': int(clinic_id),
                'default_target_date': target_date,
            }
        }

    @api.model
    def get_user_schedule_defaults(self):
        user = self.env.user
        is_manager = user.has_group('clinic_schedule.group_clinic_schedule_manager')

        # Check for saved memory state
        state = self.env['clinic.schedule.state'].search([('user_id', '=', user.id)], limit=1)

        return {
            'default_region_id': state.last_region_id if state else 0,
            'last_operated_clinic_id': state.last_clinic_id if state else 0,
            'is_tom': is_manager,
            'is_manager': is_manager,
            'user_name': user.name,
        }

    @api.model
    def save_last_operated_clinic(self, clinic_id, region_id=0):
        """Silently logs the user's last viewed clinic and region"""
        user_id = self.env.user.id
        state = self.env['clinic.schedule.state'].search([('user_id', '=', user_id)], limit=1)
        if state:
            state.write({
                'last_clinic_id': int(clinic_id),
                'last_region_id': int(region_id)
            })
        else:
            self.env['clinic.schedule.state'].create({
                'user_id': user_id,
                'last_clinic_id': int(clinic_id),
                'last_region_id': int(region_id)
            })
        return True

    def unlink(self):
        sys_bypass = self.env.su or self.env.context.get('bypass_matrix_lock')
        is_manager = self.env.user.has_group('clinic_schedule.group_clinic_schedule_manager')

        if not sys_bypass:
            for rec in self:
                # Date lock applies to EVERYONE
                if rec._is_date_locked(rec.clinic_id.id, rec.start_datetime):
                    raise ValidationError(
                        _("Schedule Locked: Cannot delete sessions from a locked date. Please unlock the matrix first."))

                # Completed status lock applies to non-managers
                if not is_manager and rec.attendance_state == 'completed':
                    raise ValidationError(
                        _("Record Locked: This session is 'Completed'. Only Managers can delete completed sessions."))

        return super().unlink()

    def action_send_test_notification(self):
        """ Manual trigger: Blocks duplicate sending and dynamically reports success/failure """
        self.ensure_one()
        if self.notification_status in ['queued', 'wa_delivered', 'sms_delivered']:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Already Dispatched'),
                    'message': _('Notification has already been sent. Modify the appointment details to re-dispatch.'),
                    'sticky': False,
                    'type': 'warning',
                }
            }

        # _send_slot_notification returns True if queued successfully, False if failed
        was_queued = self._send_slot_notification(trigger_type='booking_confirmation', force_queue=True)

        if was_queued:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Notification Queued'),
                    'message': _('WhatsApp notification has been staged in the outbox queue.'),
                    'sticky': False,
                    'type': 'success',
                }
            }
        else:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Dispatch Failed'),
                    'message': _(
                        'Could not queue notification. Ensure patient phone number and active template exist.'),
                    'sticky': False,
                    'type': 'danger',
                }
            }

    def _send_slot_notification(self, trigger_type='booking_confirmation', session=None, force_queue=False):
        self.ensure_one()
        if not self.patient_id:
            return False

        # Deduplication Guard: Block if already sent. If called by the cron, allow 'queued' to proceed.
        blocked_states = ['wa_delivered', 'sms_delivered'] if force_queue else ['queued', 'wa_delivered', 'sms_delivered']
        if self.notification_status in blocked_states:
            _logger.info("Skipping notification for appointment %s: already in state %s", self.id, self.notification_status)
            return False

        # Deduplication Guard: Never queue if already active or sent without changes
        if self.notification_status in ['queued', 'wa_delivered', 'sms_delivered']:
            _logger.info("Skipping notification for appointment %s: already in state %s", self.id, self.notification_status)
            return False

        template = self.env['whatsapp.template'].search([
            ('model_id.model', '=', self._name),
            ('active', '=', True)
        ], limit=1)

        if not template:
            _logger.warning("No active WhatsApp template configured for %s", self._name)
            self.message_post(body=_("<b>Notification Failed:</b> No active WhatsApp template found for Appointments."))
            self.write({'notification_status': 'failed'})
            return False

        created_queue = template.send_messages(self)
        if created_queue:
            self.write({'notification_status': 'queued'})
            self.message_post(body=_("<b>WhatsApp Queued:</b> Notification added to dispatch queue (Queue ID: %s).") % created_queue.id)
            return True

        self.write({'notification_status': 'failed'})
        self.message_post(body=_("<b>Notification Failed:</b> Recipient phone missing or render error."))
        return False

    @api.depends('start_datetime')
    def _compute_end_datetime(self):
        for record in self:
            record.end_datetime = record.start_datetime + timedelta(hours=1) if record.start_datetime else False

    end_datetime = fields.Datetime(string='End Time', compute='_compute_end_datetime', store=True, readonly=False,
                                   tracking=True)

    # NEW FIELDS: UI Info Banner triggers
    has_existing_session = fields.Boolean(compute='_compute_existing_session_info', store=False)
    existing_session_info = fields.Html(compute='_compute_existing_session_info', store=False)

    allowed_patient_ids = fields.Many2many('clinic.patient', compute='_compute_allowed_patient_ids')

    @api.depends('patient_id', 'start_datetime', 'slot_type')
    def _compute_existing_session_info(self):
        """ Computes and formats the 1st session details to display in the UI banner """
        for record in self:
            if record.slot_type == 'patient' and record.patient_id and record.start_datetime:
                local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
                local_dt = pytz.utc.localize(record.start_datetime).astimezone(local_tz)
                start_of_day = local_tz.localize(datetime.combine(local_dt.date(), time.min)).astimezone(
                    pytz.utc).replace(tzinfo=None)
                end_of_day = local_tz.localize(datetime.combine(local_dt.date(), time.max)).astimezone(
                    pytz.utc).replace(tzinfo=None)

                domain = [
                    ('patient_id', '=', record.patient_id.id),
                    ('slot_type', '=', 'patient'),
                    ('start_datetime', '>=', start_of_day),
                    ('start_datetime', '<=', end_of_day),
                    ('attendance_state', '!=', 'no_show')
                ]

                # Exclude self during edit
                self_id = record._origin.id or record.id
                if self_id:
                    domain.append(('id', '!=', self_id))

                existing = self.env['clinic.schedule.appointment'].search(domain, limit=1)

                if existing:
                    conflict = existing[0]
                    s_time = pytz.utc.localize(conflict.start_datetime).astimezone(local_tz).strftime('%I:%M %p')
                    e_time = pytz.utc.localize(conflict.end_datetime).astimezone(local_tz).strftime('%I:%M %p')
                    t_name = conflict.therapist_id.name if conflict.therapist_id else 'Unassigned'
                    v_type = dict(self._fields['visit_type'].selection).get(conflict.visit_type, 'Clinic')

                    record.has_existing_session = True
                    record.existing_session_info = f"""
                            <strong>Therapist:</strong> {t_name} &nbsp;|&nbsp; 
                            <strong>Time:</strong> {s_time} - {e_time} &nbsp;|&nbsp; 
                            <strong>Type:</strong> {v_type} &nbsp;|&nbsp; 
                            <strong>Location:</strong> {conflict.clinic_id.name}
                        """
                else:
                    record.has_existing_session = False
                    record.existing_session_info = False
            else:
                record.has_existing_session = False
                record.existing_session_info = False

    @api.depends('clinic_id', 'start_datetime')
    def _compute_allowed_patient_ids(self):
        """ Filters patients by clinic and daily limits. """
        for record in self:
            if record.clinic_id:
                # 1. Fetch active paid enrollments for the selected clinic
                enrollments = self.env['patient.enrollment'].search([
                    ('clinic_id', '=', record.clinic_id.id),
                    ('payment_state', '=', 'paid'),
                    ('state', '=', 'active')
                ])
                enrolled_patient_ids = enrollments.mapped('patient_id').ids

                # 2. Filter eligible patients to ONLY those belonging to THIS clinic
                # Matches either their active enrollment OR their primary clinic profile
                eligible_patients = self.env['clinic.patient'].search([
                    '|', ('id', 'in', enrolled_patient_ids), ('clinic_id', '=', record.clinic_id.id),
                    ('remaining_sessions', '>', 0)
                ])

                if record.start_datetime:
                    local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
                    local_dt = pytz.utc.localize(record.start_datetime).astimezone(local_tz)
                    start_of_day = local_tz.localize(datetime.combine(local_dt.date(), time.min)).astimezone(
                        pytz.utc).replace(tzinfo=None)
                    end_of_day = local_tz.localize(datetime.combine(local_dt.date(), time.max)).astimezone(
                        pytz.utc).replace(tzinfo=None)

                    daily_sessions = self.env['clinic.schedule.appointment'].search([
                        ('patient_id', 'in', eligible_patients.ids),
                        ('slot_type', '=', 'patient'),
                        ('start_datetime', '>=', start_of_day),
                        ('start_datetime', '<=', end_of_day),
                        ('attendance_state', '!=', 'no_show')
                    ])

                    # Exclude self so the patient doesn't disappear from the dropdown while editing their own record
                    self_id = record._origin.id or record.id
                    session_counts = {}
                    for s in daily_sessions:
                        if s.id != self_id:
                            session_counts[s.patient_id.id] = session_counts.get(s.patient_id.id, 0) + 1

                    filtered_ids = [p.id for p in eligible_patients if session_counts.get(p.id, 0) < 2]
                    record.allowed_patient_ids = self.env['clinic.patient'].browse(filtered_ids)
                else:
                    record.allowed_patient_ids = eligible_patients
            else:
                # Clear the dropdown entirely if no clinic is selected
                record.allowed_patient_ids = self.env['clinic.patient'].search([('id', '=', False)])

    @api.constrains('start_datetime', 'therapist_id')
    def _check_therapist_availability(self):
        """Hard Lock: Prevents dragging patients onto absent/locked therapists."""
        for record in self:
            if not record.therapist_id or not record.start_datetime: continue
            target_date = record.start_datetime.date()
            local_time = pytz.utc.localize(record.start_datetime).astimezone(
                pytz.timezone(self.env.user.tz or 'Asia/Kolkata'))

            state = self.env['clinic.therapist.daily.state'].search([
                ('therapist_id', '=', record.therapist_id.id),
                ('target_date', '=', target_date)
            ], limit=1)

            if state:
                if state.action_type in ['no_show', 'wo', 'leave']:
                    lbl = dict(state._fields['action_type'].selection).get(state.action_type)
                    raise ValidationError(
                        _("Availability Locked: %s is marked as '%s' for today and cannot be assigned any sessions.") % (
                            record.therapist_id.name, lbl
                        ))
                elif state.action_type == 'late' and local_time.hour < state.expected_hour:
                    expected_str = f"{state.expected_hour % 12 or 12}:00 {'AM' if state.expected_hour < 12 else 'PM'}"
                    raise ValidationError(
                        _("Availability Locked: %s is arriving late today (Expected: %s). Cannot book sessions before this time.") % (
                            record.therapist_id.name, expected_str
                        ))

    @api.constrains('start_datetime', 'end_datetime', 'therapist_id', 'clinic_id')
    def _check_therapist_overlap(self):
        """Validates Overlaps AND enforces a 1-Hour Cross-Clinic Transit Buffer. Ignores No-Shows."""
        if self.env.su or self.env.context.get('bypass_matrix_lock'):
            return
        for record in self:
            if not record.therapist_id: continue
            # 1. Exact Booking Overlap Check (IGNORE NO SHOWS)
            domain = [
                ('therapist_id', '=', record.therapist_id.id), ('id', '!=', record.id),
                ('start_datetime', '<', record.end_datetime), ('end_datetime', '>', record.start_datetime),
                ('attendance_state', '!=', 'no_show')
            ]
            conflict = self.sudo().search(domain, limit=1)
            if conflict:
                local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
                s_time = pytz.utc.localize(conflict.start_datetime).astimezone(local_tz).strftime('%I:%M %p')
                e_time = pytz.utc.localize(conflict.end_datetime).astimezone(local_tz).strftime('%I:%M %p')
                target_name = conflict.patient_id.name if conflict.slot_type == 'patient' else conflict.slot_type
                raise ValidationError(_("Operational Conflict: %s is already booked for '%s' at %s from %s to %s!") % (
                    record.therapist_id.name, target_name, conflict.clinic_id.name, s_time, e_time
                ))

            # 2. Transit Buffer Check (Requires minimum 1-hour gap between different clinics)
            if not record.clinic_id: continue

            # Find the session immediately before this one (IGNORE NO SHOWS)
            prev_app = self.sudo().search([
                ('therapist_id', '=', record.therapist_id.id), ('id', '!=', record.id),
                ('end_datetime', '<=', record.start_datetime),
                ('attendance_state', '!=', 'no_show')
            ], order='end_datetime desc', limit=1)

            if prev_app and prev_app.clinic_id.id != record.clinic_id.id:
                diff_hours = (record.start_datetime - prev_app.end_datetime).total_seconds() / 3600.0
                if diff_hours < 1.0:
                    raise ValidationError(
                        _("Transit Buffer Violation: %s requires at least 1 hour to travel from %s to %s. The current gap is only %.1f hours.") % (
                            record.therapist_id.name, prev_app.clinic_id.name, record.clinic_id.name, diff_hours
                        ))

            # Find the session immediately after this one (IGNORE NO SHOWS)
            next_app = self.sudo().search([
                ('therapist_id', '=', record.therapist_id.id), ('id', '!=', record.id),
                ('start_datetime', '>=', record.end_datetime),
                ('attendance_state', '!=', 'no_show')
            ], order='start_datetime asc', limit=1)

            if next_app and next_app.clinic_id.id != record.clinic_id.id:
                diff_hours = (next_app.start_datetime - record.end_datetime).total_seconds() / 3600.0
                if diff_hours < 1.0:
                    raise ValidationError(
                        _("Transit Buffer Violation: %s requires at least 1 hour to travel from %s to %s. The current gap is only %.1f hours.") % (
                            record.therapist_id.name, record.clinic_id.name, next_app.clinic_id.name, diff_hours
                        ))

    @api.constrains('patient_id', 'start_datetime', 'slot_type')
    def _check_daily_duplicate(self):
        """ Hard limit guardrail: Blocks any attempt to save a 3rd session on the same date. """
        for record in self:
            if record.slot_type == 'patient' and record.patient_id and record.start_datetime:
                local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
                local_dt = pytz.utc.localize(record.start_datetime).astimezone(local_tz)
                start_of_day = local_tz.localize(datetime.combine(local_dt.date(), time.min)).astimezone(
                    pytz.utc).replace(tzinfo=None)
                end_of_day = local_tz.localize(datetime.combine(local_dt.date(), time.max)).astimezone(
                    pytz.utc).replace(tzinfo=None)

                domain = [
                    ('patient_id', '=', record.patient_id.id),
                    ('slot_type', '=', 'patient'),
                    ('start_datetime', '>=', start_of_day),
                    ('start_datetime', '<=', end_of_day),
                    ('attendance_state', '!=', 'no_show')
                ]

                # Search includes the current record as it's already saved in DB during @api.constrains
                daily_sessions = self.search(domain)

                if len(daily_sessions) > 2:
                    raise ValidationError(
                        _("Maximum limit reached: %s already has 2 sessions scheduled for today. A 3rd session is not permitted.") % (
                            record.patient_id.name)
                    )

    @api.constrains('patient_id', 'therapist_id', 'slot_type')
    def _check_gender_compliance(self):
        for record in self:
            if record.slot_type == 'patient' and record.patient_id and record.therapist_id:
                t_gen = record.therapist_id.gender
                p_gen = getattr(record.patient_id, 'gender', '')
                if t_gen and p_gen:
                    t_g = t_gen.lower()
                    p_g = p_gen.lower()
                    is_male_patient = p_g in ['m', 'male', 'boy', 'man']
                    is_female_therapist = t_g in ['f', 'female', 'girl', 'woman']
                    is_female_patient = p_g in ['f', 'female', 'girl', 'woman']
                    is_male_therapist = t_g in ['m', 'male', 'boy', 'man']

                    if (is_male_patient and is_female_therapist) or (is_female_patient and is_male_therapist):
                        raise ValidationError(
                            _("Strict Compliance Error: Female therapists must be allotted to female patients, and male therapists to male patients."))

    def action_mark_no_show(self):
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        today_local = datetime.now(local_tz).date()
        for rec in self:
            if rec.start_datetime and pytz.utc.localize(rec.start_datetime).astimezone(local_tz).date() > today_local:
                raise ValidationError(_("Logical Error: You cannot mark a future session as a no-show."))
            rec.write({'attendance_state': 'no_show'})
        return True

    def action_mark_completed(self, force=False):
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        today_local = datetime.now(local_tz).date()
        for rec in self:
            if rec.start_datetime and pytz.utc.localize(rec.start_datetime).astimezone(local_tz).date() > today_local:
                raise ValidationError(_("Logical Error: You cannot mark a future session as completed."))

            # --- NEW: Backend Check & Warning ---
            if not force and rec.slot_type == 'patient' and rec.patient_id:
                rec_local_date = pytz.utc.localize(rec.start_datetime).astimezone(local_tz).date()
                has_backend_session = self.env['patient.session'].search_count([
                    ('patient_id', '=', rec.patient_id.id),
                    ('session_date', '=', rec_local_date)
                ])
                if not has_backend_session:
                    # Return the Wizard Popup Action
                    return {
                        'name': _('Confirm Force Completion'),
                        'type': 'ir.actions.act_window',
                        'res_model': 'clinic.manual.complete.wizard',
                        'view_mode': 'form',
                        'views': [[False, 'form']],
                        'target': 'new',
                        'context': {'default_appointment_id': rec.id}
                    }

            rec.write({
                'attendance_state': 'completed',
                'manual_completion_user_id': self.env.user.id
            })
        return True

    @api.model_create_multi
    def create(self, vals_list):
        # Only cron jobs and background syncs get a system bypass
        sys_bypass = self.env.su or self.env.context.get('bypass_matrix_lock')

        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('clinic.schedule.appointment') or _('New')
            if vals.get('slot_type') == 'patient' and not vals.get('patient_id'):
                raise ValidationError(_("A Patient must be selected for a Patient Session!"))

            # Hard Lock Enforcement: Block EVERYONE from booking on locked dates
            if not sys_bypass and vals.get('clinic_id') and vals.get('start_datetime'):
                if self._is_date_locked(vals['clinic_id'], vals['start_datetime']):
                    raise ValidationError(_("Schedule Locked: This clinic date is locked. Please unlock the matrix to create appointments."))

        return super().create(vals_list)

    @api.model
    def get_allotable_therapists(self, clinic_id, target_date, displayed_therapist_ids=None):
        domain = [('active', '=', True), ('state', '=', 'active')]
        if displayed_therapist_ids:
            domain.append(('id', 'not in', displayed_therapist_ids))

        therapists = self.env['clinic.therapist'].search(domain)

        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time.min)
        end_day = datetime.combine(target_date_obj, time.max)

        appointments = self.sudo().search([
            ('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day), ('therapist_id', '!=', False)
        ])

        active_today_map = {}
        for app in appointments:
            t_id = app.therapist_id.id
            if t_id not in active_today_map: active_today_map[t_id] = set()
            active_today_map[t_id].add(app.clinic_id.name)

        results = []
        for t in therapists:
            working_clinics = list(active_today_map.get(t.id, set()))
            is_working_elsewhere = len(working_clinics) > 0

            # --- NEW DATA FOR MASTER DIRECTORY FILTERS ---
            allowed_c_ids = t.allowed_branch_ids.ids
            allowed_r_ids = t.allowed_branch_ids.mapped('region_id').ids
            allowed_clinics_names = ", ".join(t.allowed_branch_ids.mapped('name'))

            results.append({
                'id': t.id,
                'name': t.name,
                'designation': t.designation,
                'gender': t.gender,
                'vendor_id': t.vendor_id or 'N/A',
                'is_working_elsewhere': is_working_elsewhere,
                'working_clinics': ', '.join(working_clinics) if is_working_elsewhere else '',
                'allowed_clinic_ids': allowed_c_ids,
                'allowed_region_ids': allowed_r_ids,
                'allowed_clinics_names': allowed_clinics_names,
                'status': 'busy' if is_working_elsewhere else 'available'
            })
        return results

    @api.model
    def apply_therapist_action(self, therapist_id, clinic_id, target_date, action, expected_arrival=10):
        """Applies Attendance Action and Auto-Unassigns Patients based on strict rules"""
        DailyState = self.env['clinic.therapist.daily.state']
        therapist = self.env['clinic.therapist'].browse(therapist_id)

        if action == 'present':
            existing = DailyState.search([('therapist_id', '=', therapist_id), ('target_date', '=', target_date)])
            if existing:
                existing.unlink()
                if therapist.exists():
                    therapist.message_post(
                        body=_("<b>Audit Log (%s):</b> Attendance restored to <b>Present</b> for %s.") % (
                            self.env.user.name, target_date
                        ))
            return {'status': 'success', 'message': f"{therapist.name} is now marked as Present."}

        # Create or Update State Record
        state_record = DailyState.search([('therapist_id', '=', therapist_id), ('target_date', '=', target_date)],
                                         limit=1)
        payload = {'action_type': action, 'expected_hour': int(expected_arrival) if action == 'late' else 0}
        if state_record:
            state_record.write(payload)
        else:
            payload.update({'therapist_id': therapist_id, 'target_date': target_date})
            DailyState.create(payload)

        action_labels = {'no_show': 'No Show', 'wo': 'Week Off (WO)', 'leave': 'On Leave',
                         'late': f'Late (Arrival {expected_arrival}:00)'}
        lbl = action_labels.get(action, action)
        if therapist.exists():
            therapist.message_post(body=_("<b>Audit Log (%s):</b> Attendance overlay set to <b>%s</b> for %s.") % (
                self.env.user.name, lbl, target_date
            ))

        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time.min)
        end_day = datetime.combine(target_date_obj, time.max)

        domain = [
            ('therapist_id', '=', therapist_id),
            ('start_datetime', '>=', start_day),
            ('start_datetime', '<=', end_day)
        ]

        appointments = self.search(domain)
        unassigned_count = 0

        if action == 'late':
            # Unassign only sessions before the expected arrival
            for app in appointments:
                local_time = fields.Datetime.context_timestamp(self, app.start_datetime)
                if local_time.hour < int(expected_arrival):
                    app.write({'therapist_id': False})
                    app.message_post(body=_(
                        "<b>System Auto-Unassigned:</b> Therapist marked as arriving late (%s:00).") % expected_arrival)
                    unassigned_count += 1
        elif action in ['no_show', 'wo', 'leave']:
            # Unassign all sessions for the day
            unassigned_count = len(appointments)
            appointments.write({'therapist_id': False})
            for app in appointments:
                app.message_post(body=_("<b>System Auto-Unassigned:</b> Therapist marked as %s.") % lbl)

        msg = f"{therapist.name} updated to {lbl}."
        if unassigned_count > 0:
            msg += f" {unassigned_count} dependent sessions were automatically moved to UNASSIGNED."

        return {'status': 'success', 'message': msg}

    @api.model
    def get_matrix_data(self, clinic_id, target_date, pulled_therapist_ids=None):
        """Get matrix data for clinic scheduling dashboard"""
        user = self.env.user
        is_manager = user.has_group('clinic_schedule.group_clinic_schedule_manager')
        total_scheduled_ids = set()

        # ==========================================
        # 1. STRICT DROPDOWN ISOLATION
        # ==========================================
        clinic_domain = []
        if not is_manager:
            allowed_ids = set()
            if hasattr(user, 'clinic_id') and user.clinic_id: allowed_ids.add(user.clinic_id.id)
            if hasattr(user, 'clinic_ids') and user.clinic_ids: allowed_ids.update(user.clinic_ids.ids)
            if hasattr(user, 'op_fund_managed_clinic_ids') and user.op_fund_managed_clinic_ids: allowed_ids.update(
                user.op_fund_managed_clinic_ids.ids)
            if hasattr(user,
                       'op_fund_ho_managed_clinic_ids') and user.op_fund_ho_managed_clinic_ids: allowed_ids.update(
                user.op_fund_ho_managed_clinic_ids.ids)
            clinic_domain = [('id', 'in', list(allowed_ids))]

        clinics_records = self.env["clinic.clinic"].sudo().search_read(clinic_domain, ["id", "name", "region_id"])

        region_domain = []
        if not is_manager and clinics_records:
            allowed_region_ids = [c['region_id'][0] for c in clinics_records if c.get('region_id')]
            region_domain = [('id', 'in', allowed_region_ids)] if allowed_region_ids else [('id', 'in', [])]

        regions_records = self.env["clinic.region"].sudo().search_read(region_domain, ["id", "name"])

        if not clinic_id and clinics_records:
            clinic_id = clinics_records[0]["id"]

        if not clinic_id or not target_date:
            return {
                "therapists": [], "appointments": [], "clinics": [], "regions": [], "selected_clinic_id": 0,
                "kpis": {"rs_count": 0, "fixed_count": 0, "floater_count": 0, "hv_count": 0,
                         "male_therapist_count": 0, "female_therapist_count": 0,
                         "male_fixed": 0, "male_floater": 0, "male_hv": 0,
                         "female_fixed": 0, "female_floater": 0, "female_hv": 0,
                         "utilization": 0, "allotted_clinic": 0,
                         "allotted_hv": 0, "self_scheduled": 0, "outstanding": 0,
                         "male_patient_count": 0, "female_patient_count": 0, "total_scheduled": len(total_scheduled_ids),}
            }

        clinic_id = int(clinic_id)
        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time(0, 0, 0))
        end_day = datetime.combine(target_date_obj, time(23, 59, 59))

        # ==========================================
        # 2. LOCAL APPOINTMENTS (Normal Security)
        # ==========================================
        # ... (Keep existing appointments_raw query) ...
        appointments_raw = self.search([
            ("clinic_id", "=", clinic_id),
            ("start_datetime", ">=", start_day),
            ("end_datetime", "<=", end_day)
        ], order="start_datetime asc")

        daily_states = self.env["clinic.therapist.daily.state"].search([("target_date", "=", target_date)])
        state_map = {s.therapist_id.id: s for s in daily_states}

        # FIX: Extract therapists who actively have sessions in the currently viewed day
        # Extract therapists who actively have sessions in the currently viewed day
        scheduled_therapist_ids = appointments_raw.mapped('therapist_id').ids
        target_date_obj = fields.Date.from_string(target_date)
        today_obj = fields.Date.context_today(self)
        base_domain = [("active", "=", True)]
        pulled_ids = pulled_therapist_ids or []

        if target_date_obj > today_obj:
            # FIX: Allow ANY therapist (Fixed, Floater, HV) allotted to this branch to appear on future boards
            matrix_condition = [
                "|", "|", "|",
                ("is_buffer", "=", True),
                ("id", "in", scheduled_therapist_ids),
                ("id", "in", pulled_ids),
                ("allowed_branch_ids", "in", clinic_id)
            ]
        else:
            # For today/past, pull everyone assigned to the branch
            matrix_condition = [
                "|", "|",
                ("is_buffer", "=", True),
                ("id", "in", scheduled_therapist_ids),
                ("allowed_branch_ids", "in", clinic_id)
            ]

        assigned_therapists = self.env["clinic.therapist"].sudo().search(base_domain + matrix_condition)

        # ==========================================
        # 3. CROSS-CLINIC APPOINTMENTS (Sudo Bypass)
        # ==========================================
        # We MUST sudo the search itself to bypass branch security rules and find the therapist's location
        cross_clinic_apps = self.sudo().search([
            ("clinic_id", "!=", clinic_id),
            ("start_datetime", ">=", start_day),
            ("end_datetime", "<=", end_day),
            ("therapist_id", "in", assigned_therapists.ids)
        ])

        all_apps_to_render = (appointments_raw | cross_clinic_apps).sudo()

        # ... (Therapist array building logic remains unchanged here) ...
        therapists = []
        unassigned_apps = appointments_raw.filtered(lambda a: not a.therapist_id)
        if unassigned_apps:
            therapists.append({
                'id': 0, 'name': '  UNASSIGNED / ACTION REQUIRED', 'designation': 'unassigned',
                'vendor_id': 'ACTION REQUIRED', 'gender_tag': '', 'raw_gender': False, 'is_buffer': False,
                'is_absent': False,
                'overlay_state': 'present', '_sort_score': 1, 'shift_timing': '', 'total_allotted_slots': 0
            })
        clinic_region_id = next(
            (c['region_id'][0] for c in clinics_records if c['id'] == int(clinic_id) and c['region_id']), False)
        for t in assigned_therapists:
            t_state = state_map.get(t.id)
            is_absent = t_state and t_state.action_type in ['no_show', 'wo', 'leave']

            # --- NEW STRICT MATRIX SORTING LOGIC ---
            # 1. Base Score by Role and Request Status
            if t.is_buffer and not is_absent:
                base_score = 10
            elif t.designation == 'fixed':
                base_score = 100
            elif t.designation == 'floater' and not t.is_floater_request:
                base_score = 200
            elif t.designation == 'floater' and t.is_floater_request:
                base_score = 300
            elif t.designation == 'hv' and not t.is_floater_request:
                base_score = 400
            elif t.designation == 'hv' and t.is_floater_request:
                base_score = 500
            else:
                base_score = 600

            # 2. Gender Modifier (Male first, then Female)
            if t.gender == 'm':
                gender_score = 1
            elif t.gender == 'f':
                gender_score = 2
            else:
                gender_score = 3

            # 3. Final Calculation (Drop absents to the very bottom)
            sort_score = base_score + gender_score
            if is_absent:
                sort_score += 1000
            # ---------------------------------------

            is_outside_base = bool(t.base_branch_id and t.base_branch_id.id != clinic_id)
            base_branch_name = t.base_branch_id.name if t.base_branch_id else ''

            # ... (keep the rest of the loop logic the exact same below this)
            t_apps = [a for a in all_apps_to_render if
                      a.therapist_id.id == t.id and a.slot_type == 'patient' and a.attendance_state != 'no_show']
            total_slots = len(t_apps)
            if total_slots > 0:
                first_app = min(t_apps, key=lambda a: a.start_datetime)
                local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
                start_dt = pytz.utc.localize(first_app.start_datetime).astimezone(local_tz)
                end_dt = start_dt + timedelta(hours=9)
                shift_timing = f"{start_dt.strftime('%I:%M %p')} - {end_dt.strftime('%I:%M %p')}"
            else:
                shift_timing = "Not Started"
            g_tag = ' (M)' if t.gender == 'm' else (' (F)' if t.gender == 'f' else '')

            therapists.append({
                'id': t.id,
                'name': f"{t.name}",
                'designation': t.designation,
                'vendor_id': t.vendor_id or 'N/A',
                'gender_tag': g_tag,
                'raw_gender': t.gender,
                'is_buffer': t.is_buffer,
                'is_absent': bool(is_absent),
                'overlay_state': t_state.action_type if t_state else 'present',
                'shift_timing': shift_timing,
                'total_allotted_slots': total_slots,
                '_sort_score': sort_score,
                # YOU MISSED THESE TWO LINES:
                'is_outside_base': is_outside_base,
                'base_branch_name': base_branch_name
            })

        therapists.sort(key=lambda x: (x["_sort_score"], x["name"]))

        patient_ids = all_apps_to_render.mapped("patient_id").ids
        patient_data = self.env["clinic.patient"].sudo().search_read(
            [("id", "in", patient_ids)], ["id", "name", "gender", "mrn", "remaining_sessions"]
        ) if patient_ids else []
        patient_map = {p["id"]: p for p in patient_data}

        slot_dict = dict(self._fields["slot_type"].selection)
        formatted_appointments = []
        scheduled_clinic_hv = 0
        scheduled_self = 0
        now_utc = datetime.utcnow()

        for app in all_apps_to_render:
            is_other_clinic = (app.clinic_id.id != clinic_id)
            local_time = fields.Datetime.context_timestamp(self, app.start_datetime) if app.start_datetime else False
            s_time_str = local_time.strftime("%I:%M %p") if local_time else ""
            e_time_str = fields.Datetime.context_timestamp(self, app.end_datetime).strftime(
                "%I:%M %p") if app.end_datetime else ""

            if local_time:
                snapped_minute = (local_time.minute // 10) * 10
                slot_key = f"{local_time.hour:02d}:{snapped_minute:02d}"
            else:
                slot_key = "00:00"

            if app.end_datetime and app.start_datetime:
                duration_mins = round((app.end_datetime - app.start_datetime).total_seconds() / 60.0)
            else:
                duration_mins = 10
            col_span = max(1, int(duration_mins) // 10)

            # ==========================================
            # 4. PAYLOAD SANITIZATION (Data Leak Prevention)
            # ==========================================
            p_gender, raw_p_gen, p_name, p_mrn = "", False, "", ""
            p_info = None

            # We ONLY map patient data if the user is authorized for this clinic.
            # If it's a cross-clinic record, it stays completely blank.
            if not is_other_clinic and app.patient_id and app.patient_id.id in patient_map:
                p_info = patient_map[app.patient_id.id]
                p_name = p_info.get("name") or ""
                p_mrn = p_info.get("mrn") or ""
                g_val = (p_info.get("gender") or "").lower()
                if g_val in ["m", "f"]:
                    p_gender = " (M)" if g_val == "m" else " (F)"
                    raw_p_gen = g_val

            display_state = app.attendance_state
            if display_state == 'scheduled' and app.start_datetime and app.end_datetime:
                if app.start_datetime <= now_utc <= app.end_datetime:
                    display_state = 'in_progress'

            requires_reallotment = False
            if not is_other_clinic:
                if app.therapist_id:
                    t_state = state_map.get(app.therapist_id.id)
                    if t_state:
                        if t_state.action_type in ["no_show", "wo", "leave"]:
                            requires_reallotment = True
                        elif t_state.action_type == "late" and local_time and local_time.hour < t_state.expected_hour:
                            requires_reallotment = True
                elif not app.therapist_id:
                    requires_reallotment = True

            if not is_other_clinic and app.slot_type == "patient" and app.therapist_id:
                if app.visit_type == "self":
                    scheduled_self += 1
                else:
                    scheduled_clinic_hv += 1

            formatted_appointments.append({
                "id": app.id,
                "therapist_id": app.therapist_id.id if app.therapist_id else 0,
                "slot_type": app.slot_type,
                "visit_type": app.visit_type,
                "slot_label": slot_dict.get(app.slot_type, app.slot_type),
                "patient_name": f"{p_name}{p_gender}" if p_name else "",
                "patient_mrn": p_mrn,
                "patient_raw_gender": raw_p_gen,
                "slot_key": slot_key,
                "col_span": col_span,
                "remaining_sessions": p_info.get("remaining_sessions", 0) if p_info else 0,
                "time_range": f"{s_time_str} - {e_time_str}" if s_time_str else "",
                "attendance_state": display_state,
                "requires_reallotment": requires_reallotment,
                "notification_status": app.notification_status,
                "is_other_clinic": is_other_clinic,
                # Safe because we pulled the record with sudo, but we scrubbed the patient info above
                "other_clinic_name": app.clinic_id.name if is_other_clinic else "",
                "is_therapist_mismatch": app.is_therapist_mismatch,
                "actual_therapist_name": app.actual_therapist_id.name if app.actual_therapist_id else "",
                "is_therapist_mismatch": app.is_therapist_mismatch,
                "actual_therapist_name": app.actual_therapist_id.name if app.actual_therapist_id else "",
                "manual_completion_user_name": app.manual_completion_user_id.name if app.manual_completion_user_id else ""

            })
        # ==========================================
        # 1. DYNAMIC PATIENT QUEUE (Unique & Unassigned Inclusive)
        # ==========================================
        valid_patient_apps = appointments_raw.filtered(
            lambda a: a.slot_type == "patient" and a.attendance_state != 'no_show' and a.patient_id
        )

        # Use sets to prevent double-counting patients taking up multiple slots
        scheduled_clinic_ids = set(
            valid_patient_apps.filtered(lambda a: a.visit_type == 'clinic').mapped('patient_id.id'))
        scheduled_hv_ids = set(
            valid_patient_apps.filtered(lambda a: a.visit_type == 'home').mapped('patient_id.id'))
        scheduled_self_ids = set(
            valid_patient_apps.filtered(lambda a: a.visit_type == 'self').mapped('patient_id.id'))


        total_scheduled_ids = scheduled_clinic_ids.union(scheduled_hv_ids).union(scheduled_self_ids)

        male_patients = sum(1 for p_id in total_scheduled_ids if
                            patient_map.get(p_id, {}).get("gender", "").lower() in ["m", "male"])
        female_patients = sum(1 for p_id in total_scheduled_ids if
                              patient_map.get(p_id, {}).get("gender", "").lower() in ["f", "female"])

        # NEW: Strictly filter eligible patients to THIS clinic only (via profile or active enrollment)
        enrollments = self.env['patient.enrollment'].sudo().search([
            ('clinic_id', '=', clinic_id),
            ('payment_state', '=', 'paid'),
            ('state', '=', 'active')
        ])
        enrolled_patient_ids = enrollments.mapped('patient_id').ids

        total_eligible_patients = set(self.env['clinic.patient'].sudo().search([
            ('remaining_sessions', '>', 0),
            '|', ('id', 'in', enrolled_patient_ids), ('clinic_id', '=', clinic_id)
        ]).ids)

        outstanding_count = len(total_eligible_patients - total_scheduled_ids)

        # ==========================================
        # 2. DYNAMIC BRANCH STAFFING (Exclude Absences)
        # ==========================================
        present_therapists = [
            t for t in assigned_therapists
            if not state_map.get(t.id) or state_map.get(t.id).action_type not in ['wo', 'leave', 'no_show']
        ]

        fixed_count = sum(1 for t in present_therapists if t.designation == 'fixed')
        floater_count = sum(1 for t in present_therapists if t.designation == 'floater')
        hv_count = sum(1 for t in present_therapists if t.designation == 'hv')

        male_therapists = sum(1 for t in present_therapists if t.gender == 'm')
        female_therapists = sum(1 for t in present_therapists if t.gender == 'f')

        # ==========================================
        # 3. UTILIZATION
        # ==========================================
        active_capacity_therapists = [t for t in present_therapists if not t.is_buffer]
        working_count = len(active_capacity_therapists)
        total_capacity_mins = working_count * 15 * 60

        # Condensed to prevent nested indentation issues
        total_booked_mins = sum(
            (
                        app.end_datetime - app.start_datetime).total_seconds() / 60.0 if app.start_datetime and app.end_datetime else 60
            for app in appointments_raw
            if
            app.slot_type == "patient" and app.therapist_id and not app.therapist_id.is_buffer and app.attendance_state != 'no_show'
        )

        utilization_pct = round((total_booked_mins / total_capacity_mins) * 100) if total_capacity_mins > 0 else 0

        # --- Fetch Pending Requests for Manager View ---
        pending_requests = []
        if is_manager:
            reqs = self.env['clinic.therapist'].sudo().search([
                ('is_floater_request', '=', True),
                ('request_state', '=', 'pending'),
                ('request_date', '=', target_date)
            ])
            for r in reqs:
                pending_requests.append({
                    'placeholder_id': r.id,
                    'clinic_name': r.request_clinic_id.name,
                    'gender': r.gender,
                    'name': r.name
                })

        # --- Fetch 'My Requests' for Clinic Admin View ---
        my_requests = []
        # We use active_test=False to retrieve requests that were archived because they were approved/rejected
        admin_reqs = self.env['clinic.therapist'].sudo().with_context(active_test=False).search([
            ('is_floater_request', '=', True),
            ('request_clinic_id', '=', clinic_id),
            ('request_date', '=', target_date)
        ])
        for r in admin_reqs:
            my_requests.append({
                'placeholder_id': r.id,
                'name': r.name,
                'designation': r.designation,
                'state': r.request_state
            })
            # Check if the currently viewed clinic & date is locked
        is_date_locked = False
        if clinic_id and target_date:
            is_date_locked = bool(self.env['clinic.schedule.lock'].search_count([
                ('clinic_id', '=', int(clinic_id)),
                ('target_date', '=', target_date),
                ('is_locked', '=', True)
            ]))

        return {
            'is_locked': is_date_locked,
            'therapists': therapists,
            'appointments': formatted_appointments,
            'pending_requests': pending_requests,
            'my_requests': my_requests,
            'clinics': clinics_records,
            'regions': regions_records,
            'selected_clinic_id': clinic_id,
            'kpis': {
                'fixed_count': fixed_count,
                'floater_count': floater_count,
                'hv_count': hv_count,
                'male_therapist_count': male_therapists,
                'female_therapist_count': female_therapists,
                'male_fixed': sum(1 for t in present_therapists if t.gender == 'm' and t.designation == 'fixed'),
                'male_floater': sum(
                    1 for t in present_therapists if t.gender == 'm' and t.designation == 'floater'),
                'male_hv': sum(1 for t in present_therapists if t.gender == 'm' and t.designation == 'hv'),
                'female_fixed': sum(1 for t in present_therapists if t.gender == 'f' and t.designation == 'fixed'),
                'female_floater': sum(
                    1 for t in present_therapists if t.gender == 'f' and t.designation == 'floater'),
                'female_hv': sum(1 for t in present_therapists if t.gender == 'f' and t.designation == 'hv'),
                'utilization': utilization_pct,
                'total_scheduled': len(total_scheduled_ids),
                'allotted_clinic': len(scheduled_clinic_ids),
                'allotted_hv': len(scheduled_hv_ids),
                'self_scheduled': len(scheduled_self_ids),
                'outstanding': outstanding_count,
                'male_patient_count': male_patients,
                'female_patient_count': female_patients
            }
        }

    @api.model
    def get_clinic_smart_view(self, clinic_id, target_date):
        if not clinic_id or not target_date: return {}
        clinic_id = int(clinic_id)

        # --- FIX: Strict Local to UTC Time Boundary Conversion ---
        target_date_obj = fields.Date.from_string(target_date)
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')

        start_of_day_local = local_tz.localize(datetime.combine(target_date_obj, time.min))
        end_of_day_local = local_tz.localize(datetime.combine(target_date_obj, time.max))

        start_day_utc = start_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
        end_day_utc = end_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)

        # Apply UTC Boundaries to DB Query
        patient_apps = self.search([
            ('clinic_id', '=', clinic_id),
            ('start_datetime', '>=', start_day_utc),
            ('end_datetime', '<=', end_day_utc),
            ('slot_type', '=', 'patient'),
            ('attendance_state', '!=', 'no_show')
        ])

        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
        absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]
        assigned_staff = self.env['clinic.therapist'].search(
            [('active', '=', True), '|', ('allowed_branch_ids', 'in', clinic_id), ('is_buffer', '=', True)])

        active_capacity_staff = assigned_staff.filtered(lambda t: t.id not in absent_staff_ids and not t.is_buffer)
        booked_t_ids = patient_apps.mapped('therapist_id.id')
        free_staff = active_capacity_staff.filtered(lambda t: t.id not in booked_t_ids)
        eligible_patient_ids = set(self.env['clinic.patient'].search([('remaining_sessions', '>', 0)]).ids)
        unallotted_count = len(
            eligible_patient_ids - set(patient_apps.filtered(lambda a: a.therapist_id).mapped('patient_id.id')))

        return {
            'total_scheduled_today': len(patient_apps.filtered(lambda a: a.therapist_id)),
            'clinic_visits_today': len(patient_apps.filtered(lambda a: a.visit_type == 'clinic' and a.therapist_id)),
            'home_visits_today': len(patient_apps.filtered(lambda a: a.visit_type == 'home' and a.therapist_id)),
            'self_visits_today': len(patient_apps.filtered(lambda a: a.visit_type == 'self' and a.therapist_id)),
            'yet_to_allot': unallotted_count,
            'free_staff': [{'name': t.name, 'designation': t.designation} for t in free_staff],
            'total_capacity': len(active_capacity_staff) * 15,
            'booked_slots': len(patient_apps.filtered(lambda a: a.therapist_id))
        }

    @api.model
    def get_roster_data(self, target_date=None, clinic_id=0):
        user = self.env.user
        is_manager = user.has_group('clinic_schedule.group_clinic_schedule_manager')
        domain = []

        # Apply strict clinic filtering
        if clinic_id:
            domain.append(('id', '=', int(clinic_id)))
        elif not is_manager:
            # Security Fallback: Only fetch allowed clinics if no specific clinic is requested
            allowed_ids = set()
            if hasattr(user, 'clinic_id') and user.clinic_id: allowed_ids.add(user.clinic_id.id)
            if hasattr(user, 'clinic_ids') and user.clinic_ids: allowed_ids.update(user.clinic_ids.ids)
            if hasattr(user, 'op_fund_managed_clinic_ids') and user.op_fund_managed_clinic_ids: allowed_ids.update(
                user.op_fund_managed_clinic_ids.ids)
            if hasattr(user,
                       'op_fund_ho_managed_clinic_ids') and user.op_fund_ho_managed_clinic_ids: allowed_ids.update(
                user.op_fund_ho_managed_clinic_ids.ids)
            domain.append(('id', 'in', list(allowed_ids)))

        clinics = self.env['clinic.clinic'].sudo().search_read(domain, ['id', 'name'])

        # Prefetch to prevent lazy loading inside loops
        therapist_records = self.env['clinic.therapist'].sudo().search(
            [('active', '=', True), ('is_buffer', '=', False)])

        clinic_active_floaters = {}
        if target_date:
            start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
            end_day = datetime.combine(fields.Date.from_string(target_date), time.max)
            appointments = self.search([
                ('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day), ('therapist_id', '!=', False)
            ])
            for app in appointments:
                if app.clinic_id.id not in clinic_active_floaters:
                    clinic_active_floaters[app.clinic_id.id] = set()
                clinic_active_floaters[app.clinic_id.id].add(app.therapist_id.id)

        # Pre-group therapists by clinic to avoid nested loops
        clinic_fixed_map = {c['id']: [] for c in clinics}
        clinic_floater_map = {c['id']: [] for c in clinics}

        for t in therapist_records:
            t_info = {
                'id': t.id,
                'name': f"{t.name} (Vendor: {t.vendor_id or 'N/A'})",
                'type': 'Fixed Therapist' if t.designation == 'fixed' else (
                    'Clinic Floater' if t.designation == 'floater' else 'HV Floater')
            }
            allowed_branches = t.allowed_branch_ids.ids
            if t.designation == 'fixed':
                for c_id in allowed_branches:
                    if c_id in clinic_fixed_map:
                        clinic_fixed_map[c_id].append(t_info)
            else:
                if allowed_branches:
                    for c_id in allowed_branches:
                        if c_id in clinic_floater_map:
                            clinic_floater_map[c_id].append(t_info)
                else:
                    # Handle Pan-India floaters
                    for c_id in clinic_floater_map.keys():
                        has_app = t.id in clinic_active_floaters.get(c_id, set()) if target_date else True
                        if has_app:
                            clinic_floater_map[c_id].append(t_info)

        roster_map = []
        for clinic in clinics:
            roster_map.append({
                'clinic_id': clinic['id'],
                'clinic_name': clinic['name'],
                'fixed_staff': clinic_fixed_map.get(clinic['id'], []),
                'floater_staff': clinic_floater_map.get(clinic['id'], [])
            })

        return roster_map

    @api.model
    def get_attendance_ledger(self, target_date):
        if not target_date: return []
        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
        end_day = datetime.combine(fields.Date.from_string(target_date), time.max)
        appointments = self.search([
            ('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day),
            ('slot_type', 'not in', ['wo', 'leave', 'blocked'])
        ], order='start_datetime asc')
        therapists_data = {}
        for app in appointments:
            if not app.therapist_id or app.therapist_id.is_buffer: continue
            t_id = app.therapist_id.id
            if t_id not in therapists_data:
                therapists_data[t_id] = {
                    'id': t_id, 'name': app.therapist_id.name,
                    'designation': dict(self.env['clinic.therapist']._fields['designation'].selection).get(
                        app.therapist_id.designation, ''),
                    'total_slots': 0, 'completed_slots': 0, 'work_hours': 0.0, 'timeline': []
                }
            therapists_data[t_id]['total_slots'] += 1
            duration = (
                               app.end_datetime - app.start_datetime).total_seconds() / 3600.0 if app.end_datetime and app.start_datetime else 0
            if app.slot_type == 'patient' and app.attendance_state == 'completed':
                therapists_data[t_id]['completed_slots'] += 1
                therapists_data[t_id]['work_hours'] += duration
            elif app.slot_type in ['lunch', 'training']:
                therapists_data[t_id]['work_hours'] += duration
            s_time = fields.Datetime.context_timestamp(self, app.start_datetime).strftime(
                '%I:%M %p') if app.start_datetime else ''
            therapists_data[t_id]['timeline'].append({
                'time': s_time, 'type': app.slot_type, 'patient_name': app.patient_id.name if app.patient_id else '',
                'state': app.attendance_state,
                'state_label': dict(self._fields['attendance_state'].selection).get(app.attendance_state, ''),
                'clinic_name': app.clinic_id.name
            })
        return list(therapists_data.values())

    @api.model
    def get_today_preview_data(self, clinic_id, target_date):
        """Generates a high-level daily briefing of capacity and unallotted patients."""

        # 1. Strict Local to UTC Time Boundary Conversion
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        target_date_obj = fields.Date.from_string(target_date)

        start_local = local_tz.localize(datetime.combine(target_date_obj, time.min))
        end_local = local_tz.localize(datetime.combine(target_date_obj, time.max))

        start_day_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)
        end_day_utc = end_local.astimezone(pytz.utc).replace(tzinfo=None)

        # 2. Fetch Today's Appointments
        daily_apps = self.search([
            ('clinic_id', '=', int(clinic_id)),
            ('start_datetime', '>=', start_day_utc),
            ('end_datetime', '<=', end_day_utc)
        ])

        patient_apps = daily_apps.filtered(
            lambda a: a.slot_type == 'patient' and a.attendance_state != 'no_show' and a.patient_id)
        scheduled_patients = patient_apps.mapped('patient_id')

        # 3. Calculate Active Staff (Total assigned to branch MINUS absent staff)
        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
        absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]

        working_staff = self.env['clinic.therapist'].search([
            ('active', '=', True),
            ('is_buffer', '=', False),
            ('allowed_branch_ids', 'in', int(clinic_id)),
            ('id', 'not in', absent_staff_ids)
        ])

        # 4. Calculate Outstanding Patient Queue strictly for this clinic
        enrollments = self.env['patient.enrollment'].sudo().search([
            ('clinic_id', '=', int(clinic_id)),
            ('payment_state', '=', 'paid'),
            ('state', '=', 'active')
        ])
        enrolled_patient_ids = enrollments.mapped('patient_id').ids

        eligible_patients = self.env['clinic.patient'].sudo().search([
            ('remaining_sessions', '>', 0),
            '|', ('id', 'in', enrolled_patient_ids), ('clinic_id', '=', int(clinic_id))
        ])

        unallotted_count = len(set(eligible_patients.ids) - set(scheduled_patients.ids))

        t_count = len(working_staff)
        p_count = len(scheduled_patients)

        return {
            'total_therapists': t_count,
            'scheduled_patients': p_count,
            'unallotted_patients': unallotted_count,
            't_to_p_ratio': f"1 : {round(p_count / t_count, 1)}" if t_count > 0 else "N/A",
            'completed_sessions': len(daily_apps.filtered(lambda a: a.attendance_state == 'completed')),
            'noshow_sessions': len(daily_apps.filtered(lambda a: a.attendance_state == 'no_show'))
        }

    @api.model
    def action_mass_send_notifications(self, clinic_id, target_date):
        """ Batch renders appointments and pushes them directly to whatsapp.message.queue """
        if not clinic_id or not target_date:
            return {'status': 'warning', 'message': _('Missing Clinic or Target Date.')}

        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time(0, 0, 0))
        end_day = datetime.combine(target_date_obj, time(23, 59, 59))

        appointments = self.search([
            ('clinic_id', '=', int(clinic_id)),
            ('start_datetime', '>=', start_day),
            ('end_datetime', '<=', end_day),
            ('slot_type', '=', 'patient'),
            ('therapist_id', '!=', False),
            ('notification_status', 'in', ['pending', 'failed'])
        ])

        if not appointments:
            return {'status': 'info', 'message': _('0 eligible appointments found for notification.')}

        template = self.env['whatsapp.template'].search([
            ('model_id.model', '=', self._name),
            ('active', '=', True)
        ], limit=1)

        if not template:
            return {'status': 'warning', 'message': _('No active WhatsApp template configured for Appointments.')}

        created_queue = template.send_messages(appointments)
        if created_queue:
            # Mark appointments that were successfully queued
            queued_app_ids = created_queue.mapped('res_id')
            appointments.filtered(lambda a: a.id in queued_app_ids).write({'notification_status': 'queued'})
            return {
                'status': 'success',
                'message': _('Successfully added %s notifications to the dispatch queue.') % len(created_queue)
            }

        return {'status': 'warning', 'message': _('Failed to queue messages (verify patient phone numbers).')}

    @api.model
    def _cron_consume_notification_queue(self):
        queued_appointments = self.search([('notification_status', '=', 'queued')], limit=100)
        if not queued_appointments:
            return True

        session = requests.Session()
        for app in queued_appointments:
            try:
                # Pass force_queue=True so the renderer doesn't block the dispatch
                app._send_slot_notification(trigger_type='booking_confirmation', session=session, force_queue=True)
            except Exception as e:
                _logger.error("Fatal transaction handling failure for app ID %s: %s", app.id, str(e))
                app.write({'notification_status': 'failed'})

            self.env.cr.commit()
        return True


class ClinicTherapistImportLog(models.Model):
    _name = 'clinic.therapist.import.log'
    _description = 'Therapist CSV Import'

    csv_file = fields.Binary(string='Upload Roster CSV File', required=True)
    file_name = fields.Char(string='File Name')
    execution_date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    state = fields.Selection([('draft', 'Draft'), ('done', 'Imported')], default='draft')
    records_processed = fields.Integer(readonly=True)

    def action_process_csv(self):
        if not self.csv_file: return
        try:
            decoded_file = base64.b64decode(self.csv_file).decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded_file = base64.b64decode(self.csv_file).decode('latin1')
        reader = csv.DictReader(io.StringIO(decoded_file))
        Therapist = self.env['clinic.therapist']
        counter = 0
        for row in reader:
            if (row.get('Status') or '').strip().lower() != 'active': continue
            vendor_name = (row.get('Vendor Name') or '').strip()
            vendor_id = (row.get('Vendor ID') or '').strip()
            if not vendor_name or not vendor_id: continue

            vertical = (row.get('Vertical') or '').strip().lower()
            designation = 'hv' if 'hv' in vertical else ('floater' if 'floater' in vertical else 'fixed')

            existing = Therapist.search(['|', ('vendor_id', '=', vendor_id), ('name', '=', vendor_name)], limit=1)
            payload = {'name': vendor_name, 'vendor_id': vendor_id, 'designation': designation}
            if existing:
                existing.write(payload)
            else:
                Therapist.create(payload)
            counter += 1
        self.write({'state': 'done', 'records_processed': counter})


class ClinicFloaterRequestWizard(models.TransientModel):
    _name = 'clinic.floater.request.wizard'
    _description = 'Request Floater Wizard'

    clinic_id = fields.Many2one('clinic.clinic', string="Clinic", required=True, tracking=True)
    target_date = fields.Date(string="Date", required=True, tracking=True)
    gender = fields.Selection([('m', 'Male'), ('f', 'Female')], string="Gender", required=True, tracking=True)
    floater_type = fields.Selection([
        ('floater', 'Clinic Floater'),
        ('hv', 'Home Visit (HV) Floater')
    ], string="Floater Type", required=True, default='floater')

    def action_submit_request(self):
        self.ensure_one()
        Therapist = self.env['clinic.therapist'].sudo()

        # --- 1. DYNAMIC CAPACITY VALIDATION ---
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        target_date_obj = fields.Date.from_string(self.target_date)
        start_local = local_tz.localize(datetime.combine(target_date_obj, time.min))
        end_local = local_tz.localize(datetime.combine(target_date_obj, time.max))
        start_day_utc = start_local.astimezone(pytz.utc).replace(tzinfo=None)
        end_day_utc = end_local.astimezone(pytz.utc).replace(tzinfo=None)

        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', self.target_date)])
        absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]

        # Route designated types based on user selection
        target_designations = ['hv'] if self.floater_type == 'hv' else ['fixed', 'floater']

        working_staff = Therapist.search([
            ('active', '=', True),
            ('is_buffer', '=', False),
            ('is_floater_request', '=', False),
            ('allowed_branch_ids', 'in', self.clinic_id.id),
            ('gender', '=', self.gender),
            ('designation', 'in', target_designations)
        ]).filtered(lambda t: t.id not in absent_staff_ids)

        # If staff of this gender & type exists, enforce capacity (0 staff = immediate exemption)
        if working_staff:
            threshold = 3 if self.floater_type == 'hv' else 6

            today_apps = self.env['clinic.schedule.appointment'].search([
                ('clinic_id', '=', self.clinic_id.id),
                ('slot_type', '=', 'patient'),
                ('attendance_state', '!=', 'no_show'),
                ('start_datetime', '>=', start_day_utc),
                ('start_datetime', '<=', end_day_utc),
                ('therapist_id', 'in', working_staff.ids)
            ])

            therapist_counts = {t.id: 0 for t in working_staff}
            for app in today_apps:
                therapist_counts[app.therapist_id.id] += 1

            underutilized = any(count < threshold for count in therapist_counts.values())

            if underutilized:
                type_str = "HV Floaters" if self.floater_type == 'hv' else "Working Therapists"
                gender_str = dict(self._fields['gender'].selection).get(self.gender)
                raise ValidationError(_(
                    f"Capacity limit not met! Existing {gender_str} {type_str} must have at least {threshold} assigned therapies before requesting another one."
                ))

        # --- 2. ENFORCE LIMITS & CREATE PLACEHOLDER ---
        self.env.cr.execute("SELECT id FROM clinic_clinic WHERE id = %s FOR UPDATE", [self.clinic_id.id])

        existing_requests = Therapist.search_count([
            ('is_floater_request', '=', True),
            ('request_clinic_id', '=', self.clinic_id.id),
            ('request_date', '=', self.target_date),
            ('gender', '=', self.gender),
            ('designation', '=', self.floater_type),
            ('active', '=', True)
        ])
        if existing_requests >= 3:
            gender_str = dict(self._fields['gender'].selection).get(self.gender)
            type_str = "HV" if self.floater_type == 'hv' else "Clinic"
            raise ValidationError(_(
                f"Maximum limit reached: Your clinic has already requested {existing_requests} {gender_str} {type_str} floaters for this date."
            ))

        gender_label = "M" if self.gender == 'm' else "F"
        type_label = "HV " if self.floater_type == 'hv' else ""
        placeholder_name = f"Requested {type_label}Floater ({gender_label})"
        time_str = fields.Datetime.now().strftime('%H%M%S')
        unique_suffix = f"{fields.Datetime.now().strftime('%H%M%S')}_{self.clinic_id.id}"
        dummy_phone = f"99{time_str}{str(self.clinic_id.id).zfill(2)}"[:10]

        placeholder = Therapist.create({
            'name': placeholder_name,
            'designation': self.floater_type,
            'gender': self.gender,
            'is_floater_request': True,
            'request_clinic_id': self.clinic_id.id,
            'request_date': self.target_date,
            'request_state': 'pending',
            'allowed_branch_ids': [(4, self.clinic_id.id)],
            'vendor_id': f'PENDING_HO_{unique_suffix}',
            'contact_number': dummy_phone,
        })

        # --- 3. DISPATCH NOTIFICATIONS ---
        target_region = self.clinic_id.region_id
        all_managers = self.env.ref('clinic_schedule.group_clinic_schedule_manager').users
        regional_managers = self.env['res.users']

        if target_region:
            for m in all_managers:
                if hasattr(m, 'region_ids') and target_region.id in m.region_ids.ids:
                    regional_managers |= m
                elif hasattr(m, 'region_id') and m.region_id.id == target_region.id:
                    regional_managers |= m
                elif hasattr(m, 'managed_region_ids') and target_region.id in m.managed_region_ids.ids:
                    regional_managers |= m

        users_to_notify = regional_managers if regional_managers else all_managers
        region_name_str = target_region.name if target_region else 'Unassigned'

        for user in users_to_notify:
            placeholder.activity_schedule(
                'mail.activity_data_todo',
                user_id=user.id,
                summary=f'{type_label}Floater Request: {self.clinic_id.name}',
                note=f"<b>{self.clinic_id.name}</b> (Region: {region_name_str}) has requested a {gender_label} {type_label}floater for {self.target_date}."
            )

        return {'type': 'ir.actions.act_window_close'}

class ClinicTherapistOffboardWizard(models.TransientModel):
    _name = 'clinic.therapist.offboard.wizard'
    _description = 'Offboard Therapist Wizard'

    therapist_id = fields.Many2one('clinic.therapist', required=True)
    leaving_date = fields.Date(string="Date of Leaving", required=True, default=fields.Date.context_today)
    leaving_reason = fields.Text(string="Reason (Optional)")

    def action_confirm_offboard(self):
        self.ensure_one()
        self.therapist_id.write({
            'state': 'offboarded',
            'active': False,
            'leaving_date': self.leaving_date,
            'leaving_reason': self.leaving_reason
        })
        self.therapist_id.message_post(
            body=f"<b>Offboarded</b><br/>Date: {self.leaving_date}<br/>Reason: {self.leaving_reason or 'None'}"
        )
        return {'type': 'ir.actions.act_window_close'}