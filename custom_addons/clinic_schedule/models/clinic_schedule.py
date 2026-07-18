import base64
import io
import csv
import json
import requests
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta, datetime, time
import pytz

_logger = logging.getLogger(__name__)


# --- -1. SYSTEM REGISTRY EXTENSIONS ---
class ResUsers(models.Model):
    _inherit = 'res.users'

    clinic_ids = fields.Many2many('clinic.clinic', string='Allowed Branches')


# --- 0. SMART HR SYNC (Background Performance Fix) ---
class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    is_clinic_therapist = fields.Boolean(string="Is Clinic Therapist", default=False, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['clinic.therapist']._sync_from_hr_employee(records)
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ['name', 'department_id', 'work_phone', 'is_clinic_therapist']):
            self.env['clinic.therapist']._sync_from_hr_employee(self)
        return res


# --- 1. REGION CATEGORIZATION & EMBEDDED CLINIC MAPPING ---
class ClinicRegion(models.Model):
    _name = 'clinic.region'
    _description = 'Clinic Region / Zone'

    name = fields.Char(string='Region Name', required=True)
    active = fields.Boolean(default=True)
    clinic_ids = fields.One2many('clinic.clinic', 'region_id', string="Assigned Clinics")


class ClinicClinic(models.Model):
    _inherit = 'clinic.clinic'
    region_id = fields.Many2one('clinic.region', string='Operating Region')

class ClinicPatient(models.Model):
    _inherit = 'clinic.patient'
    appointment_ids = fields.One2many('clinic.schedule.appointment', 'patient_id', string='Appointments & Notifications')

# --- 2. EXTENDED THERAPIST MODEL ---
class ClinicTherapist(models.Model):
    _name = 'clinic.therapist'
    _inherit = ['clinic.therapist', 'mail.thread', 'mail.activity.mixin']

    active = fields.Boolean(string="Active", default=True, tracking=True)
    employee_id = fields.Many2one('hr.employee', string="Linked Employee Account", ondelete='set null', tracking=True)
    vendor_id = fields.Char(string='Vendor ID', copy=False, tracking=True)
    contact_number = fields.Char(string="Phone Number", tracking=True)

    is_buffer = fields.Boolean(string="Is Buffer / Emergency Row", default=False, tracking=True,
                               help="Check this to permanently pin this row to the top of the clinic matrix for walk-ins.")

    gender = fields.Selection([('m', 'Male'), ('f', 'Female'), ('o', 'Other')], string="Gender", tracking=True)
    transport_type = fields.Selection([
        ('two_wheeler', 'Two-Wheeler'), ('four_wheeler', 'Four-Wheeler'),
        ('public', 'Public Transport'), ('company', 'Company Vehicle')
    ], string="Transport Type", tracking=True)

    designation = fields.Selection([
        ('rs', 'RS/Asst RS (Auto)'), ('fixed', 'Fixed Therapist (Manual)'),
        ('floater', 'Clinic Floater'), ('hv', 'HV Floater')
    ], string="Deployment Type", default='fixed', tracking=True)

    allowed_branch_ids = fields.Many2many('clinic.clinic', string="Allowed Branches", tracking=True)

    def action_toggle_buffer(self):
        for rec in self:
            rec.is_buffer = not rec.is_buffer
        return True

    @api.model
    def _sync_from_hr_employee(self, employees=None):
        if not employees:
            employees = self.env['hr.employee'].search([
                '|', ('is_clinic_therapist', '=', True),
                '|', ('department_id.name', 'ilike', 'RS'), ('department_id.name', 'ilike', 'Asst RS')
            ])

        for emp in employees:
            if not emp.is_clinic_therapist:
                if not emp.department_id:
                    continue
                dept_name = emp.department_id.name.upper()
                if 'RS' not in dept_name and 'ASST RS' not in dept_name:
                    continue

            existing = self.search([('employee_id', '=', emp.id)], limit=1)
            if not existing:
                existing = self.search([('name', '=', emp.name), ('designation', '=', 'rs')], limit=1)

            payload = {
                'name': emp.name,
                'employee_id': emp.id,
                'designation': 'rs',
                'contact_number': emp.work_phone or (existing.contact_number if existing else '')
            }
            if emp.user_id and hasattr(emp.user_id, 'clinic_ids'):
                payload['allowed_branch_ids'] = [(6, 0, emp.user_id.clinic_ids.ids)]

            if existing:
                existing.write(payload)
            else:
                self.create(payload)


# --- 3. REVERSIBLE STATE OVERLAY ENGINE ---
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


# --- 4. APPOINTMENT TRACKING & MATRIX DATA PIPELINES ---
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
    ], string="Session Status", default='scheduled', required=True, tracking=True)

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
    start_datetime = fields.Datetime(string='Start Time', required=True, default=fields.Datetime.now, tracking=True)
    end_datetime = fields.Datetime(string='End Time', compute='_compute_end_datetime', store=True, readonly=False,
                                   tracking=True)
    allowed_patient_ids = fields.Many2many('clinic.patient', compute='_compute_allowed_patient_ids')

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

        # 1. FETCH CREDENTIALS SECURELY
        params = self.env['ir.config_parameter'].sudo()
        engati_customer_id = params.get_param('engati.customer_id')
        engati_bot_key = params.get_param('engati.bot_key')
        engati_flow_key = params.get_param('engati.flow_key')
        engati_api_key = params.get_param('engati.api_key')

        if not all([engati_customer_id, engati_bot_key, engati_flow_key, engati_api_key]):
            self.message_post(body="⚠️ Notification Failed: Engati System Parameters missing.")
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
        local_dt = pytz.utc.localize(self.start_datetime).astimezone(local_tz) if self.start_datetime else datetime.now(local_tz)
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

            _logger.info("✅ ENGATI SUCCESS: %s", patient_phone)
            self.message_post(body=f"✅ <b>Engati Delivered:</b> Notification sent to {patient_phone}.")
            self.write({'notification_status': 'wa_delivered'})
            return True

        except requests.exceptions.RequestException as err:
            err_msg = err.response.text if err.response is not None else str(err)
            _logger.error("❌ ENGATI NOTIFICATION FAILURE: %s", err_msg)
            self.message_post(body=f"❌ <b>Notification Delivery Failure.</b><br/><i>Reason: {err_msg}</i>")
            self.write({'notification_status': 'failed'})
            return False

    @api.depends('start_datetime')
    def _compute_end_datetime(self):
        for record in self:
            # SHIFT: Reverted default duration back to 1 Hour (spans six 10-min blocks)
            record.end_datetime = record.start_datetime + timedelta(hours=1) if record.start_datetime else False

    @api.depends('clinic_id')
    def _compute_allowed_patient_ids(self):
        for record in self:
            if record.clinic_id:
                enrollments = self.env['patient.enrollment'].search([
                    ('clinic_id', '=', record.clinic_id.id),
                    ('payment_state', '=', 'paid'),
                    ('state', '=', 'active')
                ])
                record.allowed_patient_ids = enrollments.mapped('patient_id')
            else:
                record.allowed_patient_ids = self.env['clinic.patient'].search([])

    @api.constrains('start_datetime', 'end_datetime', 'therapist_id')
    def _check_therapist_overlap(self):
        for record in self:
            if not record.therapist_id: continue
            domain = [
                ('therapist_id', '=', record.therapist_id.id), ('id', '!=', record.id),
                ('start_datetime', '<', record.end_datetime), ('end_datetime', '>', record.start_datetime),
            ]
            if self.search(domain):
                raise ValidationError(
                    _("Operational Conflict: This therapist is already booked or blocked during this time!"))

    @api.constrains('patient_id', 'start_datetime', 'slot_type')
    def _check_daily_duplicate(self):
        for record in self:
            if record.slot_type == 'patient' and record.patient_id:
                start_date = record.start_datetime.date()
                domain = [
                    ('patient_id', '=', record.patient_id.id), ('slot_type', '=', 'patient'),
                    ('id', '!=', record.id),
                    ('start_datetime', '>=', datetime.combine(start_date, time.min)),
                    ('start_datetime', '<=', datetime.combine(start_date, time.max)),
                ]
                if self.search_count(domain) > 0:
                    raise ValidationError(
                        _("Anti-Duplicate Guardrail: %s already has a session booked today.") % record.patient_id.name)

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
        for rec in self: rec.write({'attendance_state': 'no_show'})
        return True

    def action_mark_completed(self):
        for rec in self: rec.write({'attendance_state': 'completed'})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('clinic.schedule.appointment') or _('New')
            if vals.get('slot_type') == 'patient' and not vals.get('patient_id'):
                raise ValidationError(_("A Patient must be selected for a Patient Session!"))

        records = super().create(vals_list)

        if not self.env.context.get('auto_book_run'):
            auto_book_vals = []
            for rec in records:
                if rec.slot_type == 'patient' and rec.patient_id and rec.therapist_id:
                    enrollment = self.env['patient.enrollment'].search([
                        ('patient_id', '=', rec.patient_id.id),
                        ('clinic_id', '=', rec.clinic_id.id),
                        ('payment_state', '=', 'paid'),
                        ('state', '=', 'active')
                    ], limit=1)

                    if enrollment and enrollment.remaining_sessions > 1:
                        sessions_to_book = enrollment.remaining_sessions - 1
                        current_start = rec.start_datetime
                        days_added = 0
                        sessions_booked = 0

                        while sessions_booked < sessions_to_book:
                            days_added += 1
                            if days_added > 90:
                                _logger.warning("Failsafe triggered: Could not auto-book %s sessions for patient %s within 90 days.", sessions_to_book, rec.patient_id.id)
                                break
                            next_start = current_start + timedelta(days=days_added)
                            # SHIFT: Auto-booking offset standard 1 hour
                            next_end = next_start + timedelta(hours=1)

                            if next_start.weekday() == 6: continue

                            conflict = self.env['clinic.schedule.appointment'].search_count([
                                ('therapist_id', '=', rec.therapist_id.id),
                                ('start_datetime', '<', next_end),
                                ('end_datetime', '>', next_start)
                            ])

                            local_tz = pytz.timezone(self.env.user.tz or 'Asia/Kolkata')
                            local_next_start = pytz.utc.localize(next_start).astimezone(local_tz)
                            
                            start_of_day_local = local_tz.localize(datetime.combine(local_next_start.date(), time.min))
                            end_of_day_local = local_tz.localize(datetime.combine(local_next_start.date(), time.max))
                            
                            start_of_day_utc = start_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)
                            end_of_day_utc = end_of_day_local.astimezone(pytz.utc).replace(tzinfo=None)

                            patient_conflict = self.env['clinic.schedule.appointment'].search_count([
                                ('patient_id', '=', rec.patient_id.id),
                                ('slot_type', '=', 'patient'),
                                ('start_datetime', '>=', start_of_day_utc),
                                ('start_datetime', '<=', end_of_day_utc)
                            ])

                            if conflict == 0 and patient_conflict == 0:
                                auto_book_vals.append({
                                    'clinic_id': rec.clinic_id.id,
                                    'therapist_id': rec.therapist_id.id,
                                    'patient_id': rec.patient_id.id,
                                    'slot_type': 'patient',
                                    'visit_type': rec.visit_type,
                                    'start_datetime': next_start,
                                })
                                sessions_booked += 1

            if auto_book_vals:
                self.with_context(auto_book_run=True).create(auto_book_vals)

        return records

    @api.model
    def get_allotable_therapists(self, clinic_id, target_date, displayed_therapist_ids=None):
        domain = [('active', '=', True)]
        if displayed_therapist_ids: domain.append(('id', 'not in', displayed_therapist_ids))

        therapists = self.env['clinic.therapist'].search(domain)
        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time.min)
        end_day = datetime.combine(target_date_obj, time.max)

        appointments = self.search([
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
                'id': t.id, 'name': t.name, 'designation': t.designation,
                'gender': t.gender, 'vendor_id': t.vendor_id or 'N/A',
                'badge_id': t.employee_id.barcode if t.employee_id and t.employee_id.barcode else 'N/A',
                'is_working_elsewhere': is_working_elsewhere,
                'working_clinics': ', '.join(working_clinics) if is_working_elsewhere else ''
            })
        return results

    @api.model
    def apply_therapist_action(self, therapist_id, clinic_id, target_date, action, expected_arrival=10):
        DailyState = self.env['clinic.therapist.daily.state']

        if action == 'present':
            existing = DailyState.search([('therapist_id', '=', therapist_id), ('target_date', '=', target_date)])
            existing.unlink()
            return True

        state_record = DailyState.search([('therapist_id', '=', therapist_id), ('target_date', '=', target_date)],
                                         limit=1)
        payload = {'action_type': action, 'expected_hour': int(expected_arrival) if action == 'late' else 0}

        if state_record:
            state_record.write(payload)
        else:
            payload.update({'therapist_id': therapist_id, 'target_date': target_date})
            DailyState.create(payload)

        return True

    @api.model
    def get_matrix_data(self, clinic_id, target_date, pulled_therapist_ids=None):
        regions_records = self.env['clinic.region'].search_read([], ['id', 'name'])
        clinics_records = self.env['clinic.clinic'].search_read([], ['id', 'name', 'region_id'])

        if not clinic_id and clinics_records: clinic_id = clinics_records[0]['id']
        if not clinic_id or not target_date:
            return {'therapists': [], 'appointments': [], 'clinics': clinics_records, 'regions': regions_records,
                    'selected_clinic_id': 0, 'kpis': {}}

        target_date_obj = fields.Date.from_string(target_date)
        start_day = datetime.combine(target_date_obj, time(0, 0, 0))
        end_day = datetime.combine(target_date_obj, time(23, 59, 59))

        appointments_raw = self.search([
            ('clinic_id', '=', int(clinic_id)), ('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day),
        ])

        daily_states = self.env['clinic.therapist.daily.state'].search([('target_date', '=', target_date)])
        state_map = {s.therapist_id.id: s for s in daily_states}

        assigned_therapists = self.env['clinic.therapist'].search([
            ('active', '=', True), '|', ('allowed_branch_ids', 'in', int(clinic_id)), ('is_buffer', '=', True)
        ])

        booked_therapist_ids = [app.therapist_id.id for app in appointments_raw if app.therapist_id]
        all_board_ids = list(set(booked_therapist_ids + (pulled_therapist_ids or [])))
        board_therapists = assigned_therapists | self.env['clinic.therapist'].browse(all_board_ids)

        therapists = []
        unassigned_apps = [app for app in appointments_raw if not app.therapist_id]
        if unassigned_apps:
            therapists.append({
                'id': 0, 'name': '?? UNASSIGNED / ACTION REQUIRED', 'designation': 'unassigned',
                'vendor_id': 'ACTION REQUIRED', 'badge_id': 'ACTION REQUIRED',
                'gender_tag': '', 'raw_gender': False, 'is_buffer': False, 'is_absent': False,
                'overlay_state': 'present', '_sort_score': 1
            })

        clinic_region_id = next(
            (c['region_id'][0] for c in clinics_records if c['id'] == int(clinic_id) and c['region_id']), False)

        for t in board_therapists:
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

            g_tag = ' (M)' if t.gender == 'm' else (' (F)' if t.gender == 'f' else '')
            therapists.append({
                'id': t.id, 'name': f"{t.name}", 'designation': t.designation,
                'vendor_id': t.vendor_id or 'N/A',
                'badge_id': t.employee_id.barcode if t.employee_id and t.employee_id.barcode else 'N/A',
                'gender_tag': g_tag, 'raw_gender': t.gender,
                'is_buffer': t.is_buffer,
                'is_absent': bool(is_absent),
                'overlay_state': t_state.action_type if t_state else 'present',
                '_sort_score': sort_score
            })

        therapists.sort(key=lambda x: (x['_sort_score'], x['name']))

        formatted_appointments = []
        slot_dict = dict(self._fields['slot_type'].selection)

        scheduled_clinic_hv = 0
        scheduled_self = 0

        enrollments_qs = self.env['patient.enrollment'].search([
            ('clinic_id', '=', int(clinic_id)), ('payment_state', '=', 'paid'), ('state', '=', 'active')
        ])
        enrollment_map = {e.patient_id.id: e.remaining_sessions for e in enrollments_qs}

        # N+1 Optimization: Pre-fetch patient fields
        patient_ids = appointments_raw.mapped('patient_id').ids
        patient_data = self.env['clinic.patient'].search_read(
            [('id', 'in', patient_ids)], 
            ['id', 'name', 'gender', 'mrn']
        )
        patient_map = {p['id']: p for p in patient_data}

        for app in appointments_raw:
            local_time = fields.Datetime.context_timestamp(self, app.start_datetime) if app.start_datetime else False
            s_time_str = local_time.strftime('%I:%M %p') if local_time else ''
            e_time_str = fields.Datetime.context_timestamp(self, app.end_datetime).strftime(
                '%I:%M %p') if app.end_datetime else ''

            # SHIFT: Passing exact 10-minute string key to Javascript (e.g. '07:10')
            slot_key = local_time.strftime('%H:%M') if local_time else '00:00'

            duration_mins = int((app.end_datetime - app.start_datetime).total_seconds() / 60.0) if app.end_datetime and app.start_datetime else 10
            col_span = max(1, duration_mins // 10)

            p_gender = ''
            raw_p_gen = False
            p_name = ''
            p_mrn = ''
            
            # Read straight from localized memory mapping structure
            if app.patient_id and app.patient_id.id in patient_map:
                p_info = patient_map[app.patient_id.id]
                p_name = p_info.get('name') or ''
                p_mrn = p_info.get('mrn') or ''
                g_val = (p_info.get('gender') or '').lower()
                if g_val in ['m', 'male']:
                    p_gender = ' (M)'
                    raw_p_gen = 'm'
                elif g_val in ['f', 'female']:
                    p_gender = ' (F)'
                    raw_p_gen = 'f'

            requires_reallotment = False
            if app.therapist_id:
                t_state = state_map.get(app.therapist_id.id)
                if t_state:
                    if t_state.action_type in ['no_show', 'wo', 'leave']:
                        requires_reallotment = True
                    elif t_state.action_type == 'late' and local_time and local_time.hour < t_state.expected_hour:
                        requires_reallotment = True
            elif not app.therapist_id:
                requires_reallotment = True

            if app.slot_type == 'patient' and app.therapist_id:
                if app.visit_type == 'self':
                    scheduled_self += 1
                else:
                    scheduled_clinic_hv += 1

            formatted_appointments.append({
                'id': app.id, 'therapist_id': app.therapist_id.id if app.therapist_id else 0,
                'slot_type': app.slot_type, 'visit_type': app.visit_type,
                'slot_label': slot_dict.get(app.slot_type, 'Blocked'),
                'patient_name': f"{p_name}{p_gender}" if p_name else '',
                'patient_mrn': p_mrn,
                'patient_raw_gender': raw_p_gen,
                'slot_key': slot_key,
                'col_span': col_span,  # Pass the span width to the UI
                'remaining_sessions': enrollment_map.get(app.patient_id.id, 0) if app.patient_id else 0,
                # Inject patient sessions!
                'time_range': f"{s_time_str} - {e_time_str}" if s_time_str else "",
                'attendance_state': app.attendance_state,
                'requires_reallotment': requires_reallotment,
                'notification_status': app.notification_status
            })

        enrollments = self.env['patient.enrollment'].search([
            ('clinic_id', '=', int(clinic_id)), ('payment_state', '=', 'paid'), ('state', '=', 'active')
        ])
        total_eligible_patients = set(enrollments.mapped('patient_id.id'))

        scheduled_patient_ids = set([
            app.patient_id.id for app in appointments_raw
            if app.slot_type == 'patient' and app.therapist_id
        ])

        # --- PRECISE KPI MATHEMATICS ENGINE ---
        outstanding_count = len(total_eligible_patients - scheduled_patient_ids)

        # 1. Staffing KPIs: Only count the therapists actively drawn on the board today
        rs_count = sum(1 for t in board_therapists if t.designation == 'rs')
        fixed_count = sum(1 for t in board_therapists if t.designation == 'fixed')
        floater_count = sum(1 for t in board_therapists if t.designation in ['floater', 'hv'])

        # 2. Utilization KPIs: Calculate exact booked minutes vs total available board minutes
        # We exclude Absent and Buffer staff from total capacity because they cannot take standard appointments
        active_capacity_therapists = [t for t in board_therapists if not t.is_buffer]
        working_count = len([t for t in active_capacity_therapists if
                             not state_map.get(t.id) or state_map.get(t.id).action_type not in ['no_show', 'wo',
                                                                                                'leave']])

        total_capacity_mins = working_count * 15 * 60  # 15 Hours * 60 Minutes

        total_booked_mins = 0
        for app in appointments_raw:
            if app.slot_type == 'patient' and app.therapist_id and not app.therapist_id.is_buffer:
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
                'rs_count': rs_count,
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

        enrollments = self.env['patient.enrollment'].search(
            [('clinic_id', '=', clinic_id), ('payment_state', '=', 'paid'), ('remaining_sessions', '>', 0)])

        unallotted_count = len(set(enrollments.mapped('patient_id.id')) - set(
            patient_apps.filtered(lambda a: a.therapist_id).mapped('patient_id.id')))

        return {
            'total_scheduled_today': len(patient_apps.filtered(lambda a: a.therapist_id)),
            'clinic_visits_today': len(patient_apps.filtered(lambda a: a.visit_type == 'clinic' and a.therapist_id)),
            'home_visits_today': len(patient_apps.filtered(lambda a: a.visit_type == 'home' and a.therapist_id)),
            'self_visits_today': len(patient_apps.filtered(lambda a: a.visit_type == 'self' and a.therapist_id)),
            'yet_to_allot': unallotted_count,
            'free_staff': [{'name': t.name, 'designation': t.designation} for t in free_staff],
            # SHIFT: Total capacity displayed in Hours
            'total_capacity': len(active_capacity_staff) * 15,
            'booked_slots': len(patient_apps.filtered(lambda a: a.therapist_id))
        }

    @api.model
    def get_roster_data(self, target_date=None):
        clinics = self.env['clinic.clinic'].search_read([], ['id', 'name'])
        therapist_records = self.env['clinic.therapist'].search([('active', '=', True)])

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

                if t.designation in ['fixed', 'rs']:
                    if clinic['id'] in allowed_branches:
                        fixed_staff.append({'id': t.id, 'name': f"{t.name} (Badge: {t.employee_id.barcode or 'N/A'})",
                                            'type': 'RS/Asst RS' if t.designation == 'rs' else 'Fixed Manual'})
                else:
                    if allowed_branches:
                        if clinic['id'] in allowed_branches:
                            floater_staff.append({'id': t.id, 'name': f"{t.name} (Vendor: {t.vendor_id or 'N/A'})",
                                                  'type': 'Clinic Floater' if t.designation == 'floater' else 'HV Floater'})
                    else:
                        has_app = t.id in clinic_active_floaters.get(clinic['id'], set()) if target_date else True
                        if has_app:
                            floater_staff.append({'id': t.id, 'name': f"{t.name} (Vendor: {t.vendor_id or 'N/A'})",
                                                  'type': 'Clinic Floater' if t.designation == 'floater' else 'HV Floater'})

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

    @api.model
    def get_daily_analytics(self, target_date):
        if not target_date: return {}
        start_day = datetime.combine(fields.Date.from_string(target_date), time.min)
        end_day = datetime.combine(fields.Date.from_string(target_date), time.max)

        daily_apps = self.search([('start_datetime', '>=', start_day), ('end_datetime', '<=', end_day)])

        working_therapists = daily_apps.mapped('therapist_id').filtered(lambda t: not t.is_buffer)
        male_therapists = working_therapists.filtered(lambda t: t.gender == 'm')
        female_therapists = working_therapists.filtered(lambda t: t.gender == 'f')
        vehicle_therapists = working_therapists.filtered(
            lambda t: t.transport_type in ['two_wheeler', 'four_wheeler', 'company'])

        patient_apps = daily_apps.filtered(lambda a: a.slot_type == 'patient' and a.patient_id and a.therapist_id)
        scheduled_patients = patient_apps.mapped('patient_id')

        male_patients = scheduled_patients.filtered(lambda p: getattr(p, 'gender', '') in ['m', 'male'])
        female_patients = scheduled_patients.filtered(lambda p: getattr(p, 'gender', '') in ['f', 'female'])

        active_enrollments = self.env['patient.enrollment'].search(
            [('payment_state', '=', 'paid'), ('state', '=', 'active')])
        unallotted_patients = active_enrollments.mapped('patient_id') - scheduled_patients

        enrollment_map = {e.patient_id.id: {'clinic_name': e.clinic_id.name, 'remaining_sessions': e.remaining_sessions}
                          for e in active_enrollments}

        def format_therapist(t):
            t_apps = daily_apps.filtered(lambda a: a.therapist_id.id == t.id)
            return {
                'id': t.id, 'name': t.name,
                'designation': dict(self.env['clinic.therapist']._fields['designation'].selection).get(t.designation,
                                                                                                       ''),
                'badge_vendor': t.vendor_id if t.designation in ['floater', 'hv'] else (
                    t.employee_id.barcode if t.employee_id else 'N/A'),
                'clinics': ", ".join(list(set(t_apps.mapped('clinic_id.name')))),
                'transport': dict(self.env['clinic.therapist']._fields['transport_type'].selection).get(
                    t.transport_type, 'None')
            }

        def format_patient(p, is_scheduled=True):
            enr_info = enrollment_map.get(p.id, {'clinic_name': 'Unknown', 'remaining_sessions': 0})
            if is_scheduled:
                p_apps = patient_apps.filtered(lambda a: a.patient_id.id == p.id)
                time_str = fields.Datetime.context_timestamp(self, p_apps[0].start_datetime).strftime(
                    '%I:%M %p') if p_apps else ''
            else:
                time_str = 'Pending Assignment'

            return {
                'id': p.id, 'name': p.name, 'mrn': getattr(p, 'mrn', 'N/A'),
                'clinic': p_apps[0].clinic_id.name if is_scheduled and p_apps else enr_info['clinic_name'],
                'time': time_str, 'remaining': enr_info['remaining_sessions']
            }

        t_count = len(working_therapists)
        p_count = len(scheduled_patients)
        m_t_count = len(male_therapists)
        f_t_count = len(female_therapists)

        return {
            'kpis': {
                'total_therapists': t_count, 'male_therapists': m_t_count, 'female_therapists': f_t_count,
                'vehicle_therapists': len(vehicle_therapists), 'scheduled_patients': p_count,
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
        """ Trigger notifications for all patients booked today """
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
            
        # Bulk rewrite inside a single transaction
        appointments.write({'notification_status': 'queued'})
        return {'status': 'success', 'message': f'Added {len(appointments)} notifications to dispatch queue.'}

    @api.model
    def _cron_consume_notification_queue(self):
        """ Target of a permanent, static ir.cron job configured to execute every 5 minutes """
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
            
            # Commit after each individual record execution.
            self.env.cr.commit()
        return True


# --- 5. CSV STAGING ENGINE ---
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
            if not vendor_name: continue

            vertical = (row.get('Vertical') or '').strip().lower()
            designation = 'hv' if 'hv' in vertical else ('floater' if 'floater' in vertical else None)
            if not designation: continue

            vendor_id = (row.get('Vendor ID') or '').strip()
            existing = Therapist.search(['|', ('vendor_id', '=', vendor_id), ('name', '=', vendor_name)], limit=1)
            payload = {'name': vendor_name, 'vendor_id': vendor_id, 'designation': designation}

            if existing:
                existing.write(payload)
            else:
                Therapist.create(payload)
            counter += 1

        self.write({'state': 'done', 'records_processed': counter})