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
    contact_number = fields.Char(string="Phone Number", tracking=True)
    is_buffer = fields.Boolean(string="Is Buffer / Emergency Row", default=False, tracking=True,
                               help="Check this to permanently pin this row to the top of the clinic matrix for walk-ins.")
    gender = fields.Selection([('m', 'Male'), ('f', 'Female'), ('o', 'Other')], string="Gender", tracking=True)
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
        ('rejected', 'Rejected')
    ], string="Request State", tracking=True)

    _sql_constraints = [
        ('unique_vendor_id', 'unique(vendor_id)', 'Vendor ID must be unique across all therapists!')
    ]

    def action_activate(self):
        """Completes Onboarding"""
        for rec in self:
            rec.state = 'active'
            rec.active = True

    def action_offboard(self):
        """Separates the Therapist"""
        for rec in self:
            rec.state = 'offboarded'
            # Optionally set rec.active = False here if you want them archived automatically

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

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True

class ClinicTherapistDailyState(models.Model):
    _name = 'clinic.therapist.daily.state'
    _description = 'Therapist Daily Attendance Overlay'

    therapist_id = fields.Many2one('clinic.therapist', required=True, ondelete='cascade')
    target_date = fields.Date(required=True)
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
    ], string="Slot Type", default='patient', required=True, tracking=True)
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
    start_datetime = fields.Datetime(string='Start Time', required=True, default=lambda self: self._ist_date(),
                                     tracking=True, index=True)
    end_datetime = fields.Datetime(string='End Time', compute='_compute_end_datetime', store=True, readonly=False,
                                   tracking=True)
    allowed_patient_ids = fields.Many2many('clinic.patient', compute='_compute_allowed_patient_ids')

    is_live_in_progress = fields.Boolean(compute='_compute_live_status', store=False)

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

        # 3. Pre-fetch today's absent staff
        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
        absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]

        carried_count, unassigned_count = 0, 0
        new_apps_vals = []

        for old_app in yesterday_sessions:
            if not old_app.patient_id or old_app.patient_id.id in today_patient_ids: continue
            if old_app.patient_id.remaining_sessions <= 0: continue  # Skip if package exhausted

            # Sync to exact time tomorrow
            new_start = old_app.start_datetime + timedelta(days=1)
            new_end = old_app.end_datetime + timedelta(days=1)

            target_therapist_id = old_app.therapist_id.id if old_app.therapist_id else False
            is_unassigned = False

            # Strict Conflict & Leave Checking
            if target_therapist_id:
                if target_therapist_id in absent_staff_ids:
                    target_therapist_id = False
                    is_unassigned = True
                else:
                    overlap = self.search([
                        ('therapist_id', '=', target_therapist_id),
                        ('start_datetime', '<', new_end),
                        ('end_datetime', '>', new_start),
                        ('attendance_state', '!=', 'no_show')
                    ], limit=1)
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

    @api.model
    def _cron_generate_daily_payouts(self):
        """
        Runs at 11:55 PM daily. Calculates Time-Fenced Incentives and OT,
        and routes the financial voucher to the therapist's last clinic of the day.
        """
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        target_date = datetime.now(local_tz).date()

        # 1. Calculate strict local time boundaries
        start_of_day_local = local_tz.localize(datetime.combine(target_date, time.min))
        end_of_day_local = local_tz.localize(datetime.combine(target_date, time.max))

        # 2. Convert boundaries safely to UTC for database querying
        start_day_utc = start_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
        end_day_utc = end_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)

        # Get all valid worked appointments for the day using UTC boundaries
        daily_apps = self.search([
            ('start_datetime', '>=', start_day_utc),
            ('end_datetime', '<=', end_day_utc),
            ('attendance_state', 'in', ['completed', 'in_progress']),
            ('therapist_id', '!=', False)
        ], order='start_datetime asc')

        # Group chronologically by therapist
        therapist_map = {}
        for app in daily_apps:
            t_id = app.therapist_id
            if t_id not in therapist_map:
                therapist_map[t_id] = []
            therapist_map[t_id].append(app)

        Disbursement = self.env['operational.fund.disbursement'].sudo()
        vouchers_to_create = []

        for therapist, apps in therapist_map.items():
            cumulative_hours = 0.0
            therapies_in_standard_time = 0

            # 1. Chronological Timeline Analysis
            for app in apps:
                duration_hours = (app.end_datetime - app.start_datetime).total_seconds() / 3600.0

                # Check if this appointment falls entirely or partially within the 9-hour window
                if cumulative_hours < 9.0:
                    if app.slot_type == 'patient' and app.attendance_state == 'completed':
                        therapies_in_standard_time += 1

                cumulative_hours += duration_hours

            # 2. Identify the Last Clinic
            last_clinic = apps[-1].clinic_id

            # 3. Calculate Incentive (Strictly inside the 9-hour window)
            if therapies_in_standard_time > 6:
                incentive_amount = (therapies_in_standard_time - 6) * 120.0
                vouchers_to_create.append({
                    'clinic_id': last_clinic.id,
                    'date': target_date,
                    'expense_category': 'incentive',
                    'therapist_role': therapist.designation if therapist.designation in ['home', 'fixed',
                                                                                         'floater'] else 'fixed',
                    'therapist_ref_id': therapist.id,
                    'amount': incentive_amount,
                    'is_system_generated': True,
                    'description': f"Automated Matrix Payout: Completed {therapies_in_standard_time} therapies within standard 9-hour shift. Base: 6.",
                    'state': 'waiting'  # Sends it to Custodian Dashboard
                })

            # 4. Calculate Overtime (Strictly outside the 9-hour window)
            if cumulative_hours > 9.0:
                ot_hours = cumulative_hours - 9.0
                ot_amount = round(ot_hours * 120.0, 2)
                vouchers_to_create.append({
                    'clinic_id': last_clinic.id,
                    'date': target_date,
                    'expense_category': 'overtime',
                    'therapist_role': therapist.designation if therapist.designation in ['home', 'fixed',
                                                                                         'floater'] else 'fixed',
                    'therapist_ref_id': therapist.id,
                    'amount': ot_amount,
                    'is_system_generated': True,
                    'description': f"Automated Matrix Payout: {round(ot_hours, 2)} hours of tracked Overtime.",
                    'state': 'waiting'
                })

        # Inject all validated vouchers into the operational fund
        if vouchers_to_create:
            Disbursement.create(vouchers_to_create)
            _logger.info(
                f"System Matrix generated {len(vouchers_to_create)} automated payout vouchers for {target_date}.")

    def _ist_date(self):
        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

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
    def action_remove_therapist_from_board(self, therapist_id, clinic_id, target_date):
        """Removes the branch from the therapist and unassigns all their local patients for the day."""
        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
        end_day = datetime.combine(fields.Date.from_string(target_date), time.max)

        apps = self.search([
            ('therapist_id', '=', int(therapist_id)),
            ('clinic_id', '=', int(clinic_id)),
            ('start_datetime', '>=', start_day),
            ('start_datetime', '<=', end_day)
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

        domain = [
            ('clinic_id', '=', int(clinic_id)),
            ('start_datetime', '>=', start_day),
            ('start_datetime', '<=', end_day),
            ('slot_type', '=', 'patient')
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

    @api.model
    def action_substitute_floater(self, placeholder_id, real_therapist_id):
        """ Accepts the request, moves patients to the real floater, and hides the placeholder """
        placeholder = self.env['clinic.therapist'].browse(int(placeholder_id))
        real_therapist = self.env['clinic.therapist'].browse(int(real_therapist_id))

        if not placeholder.exists() or not real_therapist.exists():
            raise ValidationError(_("Invalid therapist selection."))

        # Reassign all patients securely to the actual floater
        apps = self.search([('therapist_id', '=', placeholder.id)])
        apps.write({'therapist_id': real_therapist.id})
        for app in apps:
            app.message_post(body=_(
                "<b>Audit Log:</b> Session successfully substituted from Requested Placeholder to Real Floater: %s") % real_therapist.name)

        # Approve and archive the placeholder
        placeholder.write({
            'active': False,
            'request_state': 'approved'
        })
        return True

    @api.model
    def action_check_floater_eligibility(self, clinic_id, target_date):
        """ Checks if all working therapists have at least 6 sessions before allowing a floater request """
        if not clinic_id or not target_date:
            return False

        clinic_id = int(clinic_id)
        target_date_obj = fields.Date.from_string(target_date)

        # Proper UTC time boundary conversion for accurate DB querying
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        start_of_day_local = local_tz.localize(datetime.combine(target_date_obj, time.min))
        end_of_day_local = local_tz.localize(datetime.combine(target_date_obj, time.max))

        start_day_utc = start_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
        end_day_utc = end_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)

        # 1. Identify Absent Therapists
        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
        absent_staff_ids = [s.therapist_id.id for s in daily_states if s.action_type in ['no_show', 'wo', 'leave']]

        # 2. Get Active Standard Staff (Exclude buffers and pending floater placeholders)
        working_therapists = self.env['clinic.therapist'].search([
            ('active', '=', True),
            ('is_buffer', '=', False),
            ('is_floater_request', '=', False),
            ('allowed_branch_ids', 'in', clinic_id)
        ]).filtered(lambda t: t.id not in absent_staff_ids)

        # 3. Enforce the >= 6 Capacity Threshold
        for t in working_therapists:
            slot_count = self.search_count([
                ('clinic_id', '=', clinic_id),
                ('therapist_id', '=', t.id),
                ('slot_type', '=', 'patient'),
                ('attendance_state', '!=', 'no_show'),
                ('start_datetime', '>=', start_day_utc),
                ('start_datetime', '<=', end_day_utc)
            ])

            if slot_count < 6:
                raise ValidationError(_(
                    "Capacity threshold not met: All working therapists must have at least 6 assigned therapies before requesting a floater.\n\n"
                    f"Staff Member '{t.name}' currently only has {slot_count} therapies assigned."
                ))

        # 4. If validation passes, route to the Wizard
        return {
            'name': _('Request Floater Therapist'),
            'type': 'ir.actions.act_window',
            'res_model': 'clinic.floater.request.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'default_clinic_id': clinic_id,
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


    def write(self, vals):
        # 1. ABSOLUTE COMPLETION LOCK
        business_fields = {'therapist_id', 'start_datetime', 'clinic_id', 'patient_id', 'slot_type', 'visit_type', 'attendance_state'}
        if any(f in vals for f in business_fields):
            for rec in self:
                if rec.attendance_state == 'completed':
                    raise ValidationError(_("Record Locked: This session is 'Completed'. No further modifications are permitted."))

        for rec in self:
            audit_entries = []
            if 'therapist_id' in vals:
                old_t = rec.therapist_id.name if rec.therapist_id else 'Unassigned'
                new_t_obj = self.env['clinic.therapist'].browse(vals['therapist_id']) if vals.get(
                    'therapist_id') else False
                new_t = new_t_obj.name if new_t_obj else 'Unassigned'
                if old_t != new_t:
                    audit_entries.append(_("Therapist changed: <b>%s</b> ➔ <b>%s</b>") % (old_t, new_t))
            if 'attendance_state' in vals:
                old_st = dict(self._fields['attendance_state'].selection).get(rec.attendance_state,
                                                                              rec.attendance_state)
                new_st = dict(self._fields['attendance_state'].selection).get(vals['attendance_state'],
                                                                              vals['attendance_state'])
                if old_st != new_st:
                    audit_entries.append(_("Session Status changed: <b>%s</b> ➔ <b>%s</b>") % (old_st, new_st))
            if 'start_datetime' in vals:
                old_time = fields.Datetime.context_timestamp(self, rec.start_datetime).strftime(
                    '%d %b %Y, %I:%M %p') if rec.start_datetime else 'None'
                new_dt_obj = fields.Datetime.from_string(vals['start_datetime'])
                new_time = fields.Datetime.context_timestamp(self, new_dt_obj).strftime(
                    '%d %b %Y, %I:%M %p') if new_dt_obj else 'None'
                audit_entries.append(_("Schedule Time changed: <b>%s</b> ➔ <b>%s</b>") % (old_time, new_time))
            if 'clinic_id' in vals:
                old_c = rec.clinic_id.name if rec.clinic_id else 'None'
                new_c_obj = self.env['clinic.clinic'].browse(vals['clinic_id']) if vals.get('clinic_id') else False
                new_c = new_c_obj.name if new_c_obj else 'None'
                audit_entries.append(_("Clinic Branch changed: <b>%s</b> ➔ <b>%s</b>") % (old_c, new_c))

            if audit_entries:
                rec.message_post(body=_("<b>Audit Log (%s):</b><br/>%s") % (
                    self.env.user.name, "<br/>".join(audit_entries)
                ))
        return super().write(vals)

    def action_send_test_notification(self):
        """ Manual button trigger for sandbox testing """
        for rec in self:
            rec._send_slot_notification(trigger_type='booking_confirmation')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Test Fired'),
                'message': _('Notification payload generated and logged to chatter.'),
                'sticky': False,
                'type': 'success',
            }
        }

    def _send_slot_notification(self, trigger_type='booking_confirmation', session=None):
        """ Centralized Decoupled Notification Wrapper with Mock Fallback Logic """
        self.ensure_one()
        if not self.patient_id:
            return False

        params = self.env['ir.config_parameter'].sudo()
        engati_customer_id = params.get_param('engati.customer_id')
        engati_bot_key = params.get_param('engati.bot_key')
        engati_flow_key = params.get_param('engati.flow_key')
        engati_api_key = params.get_param('engati.api_key')

        if not all([engati_customer_id, engati_bot_key, engati_flow_key, engati_api_key]):
            self.message_post(body="Notification Failed: Engati System Parameters missing.")
            return False

        raw_phone = getattr(self.patient_id, 'mobile', '') or getattr(self.patient_id, 'phone', '')
        patient_phone = str(raw_phone).replace(" ", "").replace("-", "").strip()
        if len(patient_phone) == 10 and patient_phone.isdigit():
            patient_phone = f"+91{patient_phone}"
        elif patient_phone and not patient_phone.startswith('+'):
            patient_phone = f"+{patient_phone}"

        patient_name = self.patient_id.name
        clinic_name = self.clinic_id.name if self.clinic_id else "ResearchAyu Clinic"

        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        local_dt = pytz.utc.localize(self.start_datetime).astimezone(local_tz) if self.start_datetime else datetime.now(
            local_tz)
        slot_date = local_dt.strftime('%d %B %Y')
        slot_time = local_dt.strftime('%I:%M %p')

        therapist_name = self.therapist_id.name if self.therapist_id else "Pending Assignment"
        visit_type_label = dict(self._fields['visit_type'].selection).get(self.visit_type, 'Session')

        url = f"https://api.engati.ai/bot-api/v3.0/customer/{engati_customer_id}/bot/{engati_bot_key}/flow/{engati_flow_key}"

        if not session:
            session = requests.Session()

        try:
            engati_payload = {
                "user.channel": "whatsapp",
                "user.phone_no": patient_phone,
                "attribute_appointment_id": str(self.id),
                "attribute_patient_name": patient_name,
                "attribute_clinic_name": clinic_name,
                "attribute_slot_date": slot_date,
                "attribute_slot_time": slot_time,
                "attribute_therapist_name": therapist_name,
                "attribute_visit_type": visit_type_label
            }
            headers = {
                "Authorization": f"Basic {engati_api_key}",
                "Content-Type": "application/json"
            }
            resp_engati = session.post(
                url,
                json=engati_payload,
                headers=headers,
                timeout=5
            )
            resp_engati.raise_for_status()
            _logger.info("ENGATI SUCCESS: %s", patient_phone)
            self.message_post(body=f"<b>Engati Delivered:</b> Notification sent to {patient_phone}.")
            self.write({'notification_status': 'wa_delivered'})
            return True
        except requests.exceptions.RequestException as err:
            err_msg = err.response.text if err.response is not None else str(err)
            _logger.error("ENGATI NOTIFICATION FAILURE: %s", err_msg)
            self.message_post(body=f"<b>Notification Delivery Failure.</b><br/><i>Reason: {err_msg}</i>")
            self.write({'notification_status': 'failed'})
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
        for record in self:
            if not record.therapist_id: continue

            # 1. Exact Booking Overlap Check (IGNORE NO SHOWS)
            domain = [
                ('therapist_id', '=', record.therapist_id.id), ('id', '!=', record.id),
                ('start_datetime', '<', record.end_datetime), ('end_datetime', '>', record.start_datetime),
                ('attendance_state', '!=', 'no_show')  # NEW: Let No-Shows be overlapped
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

    def action_mark_completed(self):
        local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
        today_local = datetime.now(local_tz).date()
        for rec in self:
            if rec.start_datetime and pytz.utc.localize(rec.start_datetime).astimezone(local_tz).date() > today_local:
                raise ValidationError(_("Logical Error: You cannot mark a future session as completed."))
            rec.write({'attendance_state': 'completed'})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('clinic.schedule.appointment') or _('New')
            if vals.get('slot_type') == 'patient' and not vals.get('patient_id'):
                raise ValidationError(_("A Patient must be selected for a Patient Session!"))

        # Return the records immediately, omitting the auto_book_vals loop
        records = super().create(vals_list)
        return records

    @api.model
    def get_allotable_therapists(self, clinic_id, target_date, displayed_therapist_ids=None):
        domain = [('active', '=', True)]
        if displayed_therapist_ids: domain.append(('id', 'not in', displayed_therapist_ids))
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
            results.append({
                'id': t.id,
                'name': t.name,
                'designation': t.designation,
                'gender': t.gender,
                'vendor_id': t.vendor_id or 'N/A',
                'is_working_elsewhere': is_working_elsewhere,
                'working_clinics': ', '.join(working_clinics) if is_working_elsewhere else ''
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
                "kpis": {"rs_count": 0, "fixed_count": 0, "floater_count": 0, "utilization": 0, "total_scheduled": 0,
                         "allotted_clinic_hv": 0, "self_scheduled": 0, "outstanding": 0}
            }

        clinic_id = int(clinic_id)
        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time(0, 0, 0))
        end_day = datetime.combine(target_date_obj, time(23, 59, 59))

        # ==========================================
        # 2. LOCAL APPOINTMENTS (Normal Security)
        # ==========================================
        appointments_raw = self.search([
            ("clinic_id", "=", clinic_id),
            ("start_datetime", ">=", start_day),
            ("end_datetime", "<=", end_day)
        ], order="start_datetime asc")

        daily_states = self.env["clinic.therapist.daily.state"].search([("target_date", "=", target_date)])
        state_map = {s.therapist_id.id: s for s in daily_states}

        assigned_therapists = self.env["clinic.therapist"].sudo().search(
            [("active", "=", True), "|", ("allowed_branch_ids", "in", clinic_id), ("is_buffer", "=", True)]
        )

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
            sort_score = 5
            if t.is_buffer and not is_absent:
                sort_score = 2
            elif is_absent:
                sort_score = 6
            elif t.designation in ['rs', 'fixed'] and int(clinic_id) in t.allowed_branch_ids.ids:
                sort_score = 3
            elif clinic_region_id and any(b.region_id.id == clinic_region_id for b in t.allowed_branch_ids):
                sort_score = 4

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
                'id': t.id, 'name': f"{t.name}", 'designation': t.designation, 'vendor_id': t.vendor_id or 'N/A',
                'gender_tag': g_tag, 'raw_gender': t.gender, 'is_buffer': t.is_buffer, 'is_absent': bool(is_absent),
                'overlay_state': t_state.action_type if t_state else 'present', 'shift_timing': shift_timing,
                'total_allotted_slots': total_slots, '_sort_score': sort_score
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
                "other_clinic_name": app.clinic_id.name if is_other_clinic else ""
            })

        # Calculate KPIs (local clinic only)
        scheduled_patient_ids = set(
            app.patient_id.id for app in appointments_raw if app.slot_type == "patient" and app.therapist_id)
        total_eligible_patients = set(self.env['clinic.patient'].search([('remaining_sessions', '>', 0)]).ids)
        outstanding_count = len(total_eligible_patients - scheduled_patient_ids)
        fixed_count = sum(1 for t in assigned_therapists if t.designation == 'fixed')
        floater_count = sum(1 for t in assigned_therapists if t.designation in ['floater', 'hv'])
        active_capacity_therapists = [t for t in assigned_therapists if not t.is_buffer]
        working_count = len([t for t in active_capacity_therapists if
                             not state_map.get(t.id) or state_map.get(t.id).action_type not in ["no_show", "wo",
                                                                                                "leave"]])
        total_capacity_mins = working_count * 15 * 60

        total_booked_mins = 0
        for app in appointments_raw:
            if app.slot_type == "patient" and app.therapist_id and not app.therapist_id.is_buffer:
                duration = (
                                       app.end_datetime - app.start_datetime).total_seconds() / 60.0 if app.start_datetime and app.end_datetime else 60
                total_booked_mins += duration

        utilization_pct = round((total_booked_mins / total_capacity_mins) * 100) if total_capacity_mins > 0 else 0

        return {
            'therapists': therapists,
            'appointments': formatted_appointments,
            'clinics': clinics_records,
            'regions': regions_records,
            'selected_clinic_id': clinic_id,
            'kpis': {
                'fixed_count': fixed_count,
                'floater_count': floater_count,
                'utilization': utilization_pct,
                'total_scheduled': scheduled_clinic_hv + scheduled_self,
                'allotted_clinic_hv': scheduled_clinic_hv,
                'self_scheduled': scheduled_self,
                'outstanding': outstanding_count
            }
        }

    @api.model
    def get_clinic_smart_view(self, clinic_id, target_date):
        if not clinic_id or not target_date: return {}
        clinic_id = int(clinic_id)
        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
        end_day = datetime.combine(fields.Date.from_string(target_date), time.max)
        patient_apps = self.search(
            [('clinic_id', '=', clinic_id), ('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day),
             ('slot_type', '=', 'patient')])
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
    def get_roster_data(self, target_date=None):
        clinics = self.env['clinic.clinic'].sudo().search_read([], ['id', 'name'])
        therapist_records = self.env['clinic.therapist'].sudo().search([('active', '=', True)])
        clinic_active_floaters = {}
        if target_date:
            start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
            end_day = datetime.combine(fields.Date.from_string(target_date), time.max)
            appointments = self.search(
                [('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day), ('therapist_id', '!=', False)])
            for app in appointments:
                if app.clinic_id.id not in clinic_active_floaters: clinic_active_floaters[app.clinic_id.id] = set()
                clinic_active_floaters[app.clinic_id.id].add(app.therapist_id.id)
        roster_map = []
        for clinic in clinics:
            fixed_staff, floater_staff = [], []
            for t in therapist_records:
                if t.is_buffer: continue
                allowed_branches = t.allowed_branch_ids.ids
                if t.designation == 'fixed':
                    if clinic['id'] in allowed_branches:
                        fixed_staff.append({
                            'id': t.id,
                            'name': f"{t.name} (Vendor: {t.vendor_id or 'N/A'})",
                            'type': 'Fixed Therapist'
                        })
                else:
                    if allowed_branches:
                        if clinic['id'] in allowed_branches:
                            floater_staff.append({
                                'id': t.id,
                                'name': f"{t.name} (Vendor: {t.vendor_id or 'N/A'})",
                                'type': 'Clinic Floater' if t.designation == 'floater' else 'HV Floater'
                            })
                    else:
                        has_app = t.id in clinic_active_floaters.get(clinic['id'], set()) if target_date else True
                        if has_app:
                            floater_staff.append({
                                'id': t.id,
                                'name': f"{t.name} (Vendor: {t.vendor_id or 'N/A'})",
                                'type': 'Clinic Floater' if t.designation == 'floater' else 'HV Floater'
                            })
            roster_map.append({'clinic_id': clinic['id'], 'clinic_name': clinic['name'], 'fixed_staff': fixed_staff,
                               'floater_staff': floater_staff})
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


        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
        end_day = datetime.combine(fields.Date.from_string(target_date), time.max)

        daily_apps = self.search([('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day)])
        working_therapists = daily_apps.mapped('therapist_id').filtered(lambda t: not t.is_buffer)

        male_therapists = working_therapists.filtered(lambda t: t.gender == 'm')
        female_therapists = working_therapists.filtered(lambda t: t.gender == 'f')
        vehicle_therapists = working_therapists.filtered(
            lambda t: t.transport_type in ['two_wheeler', 'four_wheeler', 'company']
        )

        patient_apps = daily_apps.filtered(lambda a: a.slot_type == 'patient' and a.patient_id and a.therapist_id)
        scheduled_patients = patient_apps.mapped('patient_id')
        male_patients = scheduled_patients.filtered(lambda p: getattr(p, 'gender', '') in ['m', 'male'])
        female_patients = scheduled_patients.filtered(lambda p: getattr(p, 'gender', '') in ['f', 'female'])

        # Pull eligible patients directly from clinic.patient
        all_eligible_patients = self.env['clinic.patient'].search([('remaining_sessions', '>', 0)])
        unallotted_patients = all_eligible_patients - scheduled_patients

        def format_therapist(t):
            t_apps = daily_apps.filtered(lambda a: a.therapist_id.id == t.id)
            return {
                'id': t.id,
                'name': t.name,
                'designation': dict(self.env['clinic.therapist']._fields['designation'].selection).get(t.designation,
                                                                                                       ''),
                'badge_vendor': t.vendor_id or 'N/A',
                'clinics': ", ".join(list(set(t_apps.mapped('clinic_id.name')))),
                'transport': dict(self.env['clinic.therapist']._fields['transport_type'].selection).get(
                    t.transport_type, 'None')
            }

        def format_patient(p, is_scheduled=True):
            if is_scheduled:
                p_apps = patient_apps.filtered(lambda a: a.patient_id.id == p.id)
                time_str = fields.Datetime.context_timestamp(self, p_apps[0].start_datetime).strftime(
                    '%I:%M %p') if p_apps else ''
                clinic_name = p_apps[0].clinic_id.name if p_apps else (
                    p.clinic_id.name if getattr(p, 'clinic_id', False) else 'Unknown')
            else:
                time_str = 'Pending Assignment'
                clinic_name = p.clinic_id.name if getattr(p, 'clinic_id', False) else 'Unknown'

            return {
                'id': p.id,
                'name': p.name,
                'mrn': getattr(p, 'mrn', 'N/A'),
                'clinic': clinic_name,
                'time': time_str,
                'remaining': getattr(p, 'remaining_sessions', 0)
            }

        t_count = len(working_therapists)
        p_count = len(scheduled_patients)
        m_t_count = len(male_therapists)
        f_t_count = len(female_therapists)

        return {
            'kpis': {
                'total_therapists': t_count,
                'male_therapists': m_t_count,
                'female_therapists': f_t_count,
                'vehicle_therapists': len(vehicle_therapists),
                'scheduled_patients': p_count,
                'unallotted_patients': len(unallotted_patients),
                't_to_p_ratio': f"1 : {round(p_count / t_count, 1)}" if t_count > 0 else "N/A",
                'm_to_m_ratio': f"1 : {round(len(male_patients) / m_t_count, 1)}" if m_t_count > 0 else "N/A",
                'f_to_f_ratio': f"1 : {round(len(female_patients) / f_t_count, 1)}" if f_t_count > 0 else "N/A",
                'completed_sessions': len(daily_apps.filtered(lambda a: a.attendance_state == 'completed')),
                'noshow_sessions': len(daily_apps.filtered(lambda a: a.attendance_state == 'no_show'))
            },
            'drill_downs': {
                'total_therapists': [format_therapist(t) for t in working_therapists],
                'male_therapists': [format_therapist(t) for t in male_therapists],
                'female_therapists': [format_therapist(t) for t in female_therapists],
                'vehicle_therapists': [format_therapist(t) for t in vehicle_therapists],
                'scheduled_patients': [format_patient(p, True) for p in scheduled_patients],
                'unallotted_patients': [format_patient(p, False) for p in unallotted_patients],
            }
        }

    @api.model
    def action_mass_send_notifications(self, clinic_id, target_date):
        if not clinic_id or not target_date:
            return False
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
            return {'status': 'success', 'message': '0 new notifications to send.'}

        appointments.write({'notification_status': 'queued'})
        return {'status': 'success', 'message': f'Added {len(appointments)} notifications to dispatch queue.'}

    @api.model
    def _cron_consume_notification_queue(self):
        queued_appointments = self.search([('notification_status', '=', 'queued')], limit=100)
        if not queued_appointments:
            return True

        session = requests.Session()
        for app in queued_appointments:
            try:
                app._send_slot_notification(trigger_type='booking_confirmation', session=session)
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
    _description = 'Request Floater Therapist Wizard'

    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True)
    target_date = fields.Date(string='Target Date', required=True)
    gender = fields.Selection([('m', 'Male'), ('f', 'Female')], string='Required Gender', required=True)

    def action_submit_request(self):
        self.ensure_one()
        Therapist = self.env['clinic.therapist']

        # 1. Apply PostgreSQL row-level lock on the Clinic to serialize concurrent requests
        self.clinic_id.with_for_update().read(['id'])

        # 2. Enforce the Max 3 limit per gender per day safely
        existing_requests = Therapist.search_count([
            ('is_floater_request', '=', True),
            ('request_clinic_id', '=', self.clinic_id.id),
            ('request_date', '=', self.target_date),
            ('gender', '=', self.gender),
            ('active', '=', True)
        ])

        if existing_requests >= 3:
            gender_str = dict(self._fields['gender'].selection).get(self.gender)
            raise ValidationError(_(
                f"Maximum limit reached: Your clinic has already requested {existing_requests} {gender_str} floaters for this date.\n"
                "You cannot request more than 3 per gender."
            ))

        # 2. Create the Placeholder Therapist Row
        gender_label = "M" if self.gender == 'm' else "F"
        placeholder_name = f"Requested Floater ({gender_label})"

        # FIX: Generate a highly unique Vendor ID so the SQL constraint doesn't crash on multiple requests!
        unique_suffix = fields.Datetime.now().strftime('%H%M%S')

        placeholder = Therapist.create({
            'name': placeholder_name,
            'designation': 'floater',
            'gender': self.gender,
            'is_floater_request': True,
            'request_clinic_id': self.clinic_id.id,
            'request_date': self.target_date,
            'request_state': 'pending',
            'allowed_branch_ids': [(4, self.clinic_id.id)],
            'vendor_id': f'PENDING_HO_{unique_suffix}',  # Dynamic ID prevents PostgreSQL duplicate key crash
            'contact_number': '0000000000',  # FIX: Bypasses the mandatory phone number validation
        })

        # 3. Notify Managers via To-Do Activity (Merged Access)
        managers = self.env.ref('clinic_schedule.group_clinic_schedule_manager').users
        for manager in managers:
            placeholder.activity_schedule(
                'mail.activity_data_todo',
                user_id=manager.id,
                summary='Floater Request Requires Substitution',
                note=f"<b>{self.clinic_id.name}</b> has requested a {gender_label} floater for {self.target_date}. Please substitute this request with a real floater on the Matrix Board."
            )

        return {'type': 'ir.actions.act_window_close'}
