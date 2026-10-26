import json
import logging
import re
from datetime import datetime, timedelta, time
from jinja2.sandbox import SandboxedEnvironment
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class WhatsappTemplate(models.Model):
    _name = 'whatsapp.template'
    _description = 'Meta WhatsApp Message Template'

    name = fields.Char(string='Internal Template Name', required=True)
    meta_template_name = fields.Char(
        string='Meta Approved Template Name',
        required=True,
        help="Exact approved slug in Meta (e.g. 'patient_invoice_receipt', 'therapy_comm')"
    )
    language_code = fields.Char(string='Language Code', default='en', required=True)
    model_id = fields.Many2one('ir.model', string='Applies To', required=True, ondelete='cascade')
    model = fields.Char(related='model_id.model', string='Model Name', readonly=True, store=True)
    phone_field = fields.Char(
        string='Phone Number Field',
        required=True,
        default='partner_id.mobile,partner_id.phone,patient_id.mobile,patient_id.phone,mobile,phone',
        help="Comma-separated field paths evaluated in order."
    )

    # Multi-Sender Routing
    sender_routing = fields.Selection([
        ('template', 'Template Dedicated Line'),
        ('clinic', 'Clinic Branch Line (Fallback to Template/Default)'),
        ('default', 'System Default Line'),
    ], string='Sender Routing Strategy', default='template', required=True)

    sender_phone_id = fields.Many2one(
        'whatsapp.sender.phone',
        string='Designated Sender Line',
        help="Select the exact WhatsApp line for this template. If in draft/unregistered on Meta, falls back safely to default line."
    )

    # Advanced Timing
    dispatch_timing = fields.Selection([
        ('immediate', 'Immediate (Real-Time Synchronous)'),
        ('relative_delay', 'Relative Delay from Trigger'),
        ('time_of_day', 'Fixed Time of Day (e.g., 09:00 AM)'),
        ('field_offset', 'Relative to Record Datetime Field (e.g., Appointment Time)'),
    ], string='Timing Mode', default='immediate', required=True)

    delay_value = fields.Float(string='Delay Amount', default=1.0)
    delay_unit = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
    ], string='Delay Unit', default='hours')

    time_of_day_hour = fields.Float(string='Send At Hour', default=9.0)
    time_of_day_offset_days = fields.Integer(string='Day Offset', default=0)

    record_date_field = fields.Char(string='Record Date/Datetime Field')
    record_offset_direction = fields.Selection([
        ('before', 'Before Scheduled Date/Time'),
        ('after', 'After Scheduled Date/Time'),
    ], string='Offset Direction', default='before')
    record_offset_value = fields.Float(string='Offset Amount', default=2.0)
    record_offset_unit = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
    ], string='Offset Unit', default='hours')

    auto_send_on_post = fields.Boolean(
        string='Auto-Send on Confirmation/Posting',
        default=False,
        help="Automatically stage or dispatch whenever a record of this model is posted/confirmed."
    )

    header_type = fields.Selection([
        ('none', 'None / Static Text Header'),
        ('document', 'Dynamic Document (PDF) Link'),
        ('image', 'Dynamic Image Link'),
    ], string='Header Type', default='none', required=True)
    header_document_url = fields.Char(string='Header Document URL Expression')
    header_document_filename = fields.Char(string='Header Document Filename', default='Document.pdf')

    param_ids = fields.One2many(
        'whatsapp.template.param',
        'template_id',
        string='Template Parameters',
        copy=True
    )
    body_preview = fields.Text(string='Message Preview / Documentation')
    active = fields.Boolean(default=True)

    action_server_id = fields.Many2one(
        'ir.actions.server',
        string='Generated Server Action',
        readonly=True,
        ondelete='set null'
    )

    def action_create_action(self):
        self.ensure_one()
        if self.action_server_id:
            return
        action = self.env['ir.actions.server'].sudo().create({
            'name': f"Send WhatsApp: {self.name}",
            'model_id': self.model_id.id,
            'binding_model_id': self.model_id.id,
            'binding_view_types': 'list,form',
            'state': 'code',
            'code': f"""
template = env['whatsapp.template'].browse({self.id})
if template.exists():
    template.send_messages(records)
""",
        })
        self.action_server_id = action.id
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Action Button Created'),
                'message': _("Template added to the 'Action' menu on %s.") % self.model_id.name,
                'type': 'success',
            }
        }

    def action_remove_action(self):
        self.ensure_one()
        if self.action_server_id:
            self.action_server_id.sudo().unlink()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Action Removed'),
                'message': _("Action menu item removed from %s.") % self.model_id.name,
                'type': 'info',
            }
        }

    def _resolve_field_value(self, record, field_path):
        for candidate in (field_path or '').split(','):
            current = record
            for part in candidate.strip().split('.'):
                if not current:
                    break
                current = getattr(current, part, False)
            if current:
                return str(current).strip()
        return False

    def _detect_clinic(self, record):
        """Deeply resolves the clinic where the patient is registered/treated."""
        # 1. Direct record branch
        for attr in ['clinic_id', 'clinic', 'branch_id']:
            val = getattr(record, attr, False)
            if val and getattr(val, '_name', '') == 'clinic.clinic':
                return val

        # 2. Patient registration branch
        if hasattr(record, 'patient_id') and record.patient_id:
            pat_clinic = getattr(record.patient_id, 'clinic_id', False)
            if pat_clinic and getattr(pat_clinic, '_name', '') == 'clinic.clinic':
                return pat_clinic

        # 3. POS Order branch
        if hasattr(record, 'pos_order_ids') and record.pos_order_ids:
            pos = record.pos_order_ids[0]
            for attr in ['clinic_id', 'branch_id']:
                val = getattr(pos, attr, False)
                if val and getattr(val, '_name', '') == 'clinic.clinic':
                    return val
            if hasattr(pos, 'config_id') and getattr(pos.config_id, 'clinic_id', False):
                return pos.config_id.clinic_id

        # 4. Partner linked home branch or patient profile
        for rel in ['partner_id']:
            partner = getattr(record, rel, False)
            if partner:
                if getattr(partner, 'clinic_id', False) and getattr(partner.clinic_id, '_name', '') == 'clinic.clinic':
                    return partner.clinic_id
                # Trace linked clinic.patient
                pat = self.env['clinic.patient'].sudo().search([('partner_id', '=', partner.id)], limit=1)
                if pat and pat.clinic_id:
                    return pat.clinic_id

        return self.env['clinic.clinic'].browse()

    def _resolve_sender_phone_id(self, clinic):
        """Resolves the Meta Phone Number ID. Falls back gracefully if designated sender is unregistered/draft."""
        ICP = self.env['ir.config_parameter'].sudo()
        global_fallback = (ICP.get_param('clinic_whatsapp.phone_number_id') or '1395062320356609').strip()

        # 1. Clinic Branch Override (if active)
        if self.sender_routing == 'clinic' and clinic:
            if getattr(clinic, 'sender_phone_id', False) and clinic.sender_phone_id.state == 'active' and clinic.sender_phone_id.phone_number_id:
                return clinic.sender_phone_id.phone_number_id.strip()

        # 2. Template Designated Sender (if active)
        if self.sender_phone_id and self.sender_phone_id.state == 'active' and self.sender_phone_id.phone_number_id:
            return self.sender_phone_id.phone_number_id.strip()

        # 3. Default Sender Phone Record
        default_sender = self.env['whatsapp.sender.phone'].sudo().search([('is_default', '=', True)], limit=1)
        if default_sender and default_sender.state == 'active' and default_sender.phone_number_id:
            return default_sender.phone_number_id.strip()

        # Safe Fallback to Global Settings ID (Primary Central Line)
        _logger.info("Designated WhatsApp sender is in draft or unverified on Meta. Gracefully routing via verified primary line.")
        return global_fallback

    def _calculate_target_send_datetime(self, record):
        now_dt = fields.Datetime.now()
        if self.dispatch_timing == 'immediate':
            return now_dt
        if self.dispatch_timing == 'relative_delay':
            val = self.delay_value or 0.0
            if self.delay_unit == 'minutes':
                return now_dt + timedelta(minutes=val)
            elif self.delay_unit == 'hours':
                return now_dt + timedelta(hours=val)
            elif self.delay_unit == 'days':
                return now_dt + timedelta(days=val)
        if self.dispatch_timing == 'time_of_day':
            hours = int(self.time_of_day_hour)
            minutes = int((self.time_of_day_hour - hours) * 60)
            target_date = (fields.Date.today() + timedelta(days=self.time_of_day_offset_days or 0))
            naive_dt = datetime.combine(target_date, time(hour=hours, minute=minutes))
            return fields.Datetime.to_string(naive_dt)
        if self.dispatch_timing == 'field_offset' and self.record_date_field:
            raw_field_val = False
            for part in self.record_date_field.split('.'):
                raw_field_val = getattr(record, part, False) if not raw_field_val else getattr(raw_field_val, part, False)
                if not raw_field_val:
                    break
            if raw_field_val:
                if isinstance(raw_field_val, str):
                    raw_field_val = fields.Datetime.from_string(raw_field_val)
                elif isinstance(raw_field_val, fields.Date):
                    raw_field_val = datetime.combine(raw_field_val, time(hour=9, minute=0))
                val = self.record_offset_value or 0.0
                delta = timedelta(minutes=val) if self.record_offset_unit == 'minutes' else \
                        timedelta(hours=val) if self.record_offset_unit == 'hours' else \
                        timedelta(days=val)
                return raw_field_val - delta if self.record_offset_direction == 'before' else raw_field_val + delta
        return now_dt

    def send_messages(self, records):
        self.ensure_one()
        if not records:
            return self.env['whatsapp.message.queue']

        env = SandboxedEnvironment()
        ICP = self.env['ir.config_parameter'].sudo()
        public_base = (ICP.get_param('clinic_whatsapp.public_base_url') or ICP.get_param('web.base.url', '')).rstrip('/')

        def format_datetime(dt, fmt='%d %b %Y, %I:%M %p'):
            if not dt:
                return ''
            if isinstance(dt, str):
                dt = fields.Datetime.from_string(dt)
            return fields.Datetime.context_timestamp(records[0], dt).strftime(fmt)

        def format_date(d, fmt='%d %b %Y'):
            if not d:
                return ''
            if isinstance(d, str):
                d = fields.Date.from_string(d)
            return d.strftime(fmt)

        queue_vals = []
        for rec in records:
            raw_phone = self._resolve_field_value(rec, self.phone_field)
            if not raw_phone:
                _logger.warning("No phone number found for %s (ID %s)", rec._name, rec.id)
                continue

            clinic = self._detect_clinic(rec)
            sender_phone_id = self._resolve_sender_phone_id(clinic)
            target_send_dt = self._calculate_target_send_datetime(rec)

            name_str = (getattr(rec, 'patient_id', False) and rec.patient_id.name) or \
                       (getattr(rec, 'partner_id', False) and rec.partner_id.name) or \
                       getattr(rec, 'name', 'Valued Patient')

            if hasattr(rec, '_portal_ensure_token'):
                rec._portal_ensure_token()

            # Dynamic Clinic Admin Resolution for Jinja
            # Dynamic Clinic Admin Resolution for Jinja (Safe Attribute Access)
            adm_name = getattr(clinic, 'admin_name', False) or 'Clinic Manager'
            adm_phone = getattr(clinic, 'admin_phone', False) or getattr(clinic, 'helpline_phone', False) or (
                        getattr(clinic, 'phone', False) or '9082777624')
            adm_wa = getattr(clinic, 'admin_whatsapp', False) or adm_phone
            clean_wa = re.sub(r'\D', '', adm_wa or '')[-10:]
            adm_chat_link = f"https://wa.me/91{clean_wa}" if clean_wa else ""

            render_context = {
                'object': rec,
                'clinic': clinic,
                'format_date': format_date,
                'format_datetime': format_datetime,
                'base_url': public_base,
                'admin_name': adm_name,
                'admin_phone': adm_phone,
                'admin_whatsapp': adm_wa,
                'admin_chat_link': adm_chat_link,
            }

            body_parameters = []
            for param in self.param_ids.sorted(key=lambda p: p.index):
                try:
                    expr_tmpl = env.from_string(param.value_expression or '')
                    rendered_val = expr_tmpl.render(render_context).strip()
                    body_parameters.append(rendered_val)
                except Exception as e:
                    _logger.error("Error rendering param {{%s}} on template %s: %s", param.index, self.name, str(e))
                    body_parameters.append('')

            meta_components_payload = {'body_params': body_parameters}

            if self.header_type == 'document' and self.header_document_url:
                try:
                    doc_url_tmpl = env.from_string(self.header_document_url)
                    doc_name_tmpl = env.from_string(self.header_document_filename or 'Document.pdf')
                    meta_components_payload['header_document'] = {
                        'url': doc_url_tmpl.render(render_context).strip(),
                        'filename': doc_name_tmpl.render(render_context).strip(),
                    }
                except Exception as e:
                    _logger.error("Failed rendering header document for %s: %s", self.name, str(e))

            preview_text = ''
            if self.body_preview:
                try:
                    preview_text = env.from_string(self.body_preview).render(render_context)
                except Exception:
                    preview_text = f"Template: {self.meta_template_name}"

            queue_entry = {
                'template_id': self.id,
                'meta_template_name': self.meta_template_name,
                'language_code': self.language_code or 'en',
                'phone_number_id': sender_phone_id,
                'clinic_id': clinic.id if clinic else False,
                'patient_name': name_str,
                'phone': str(raw_phone),
                'message_body': preview_text,
                'params_json': json.dumps(meta_components_payload),
                'res_model': rec._name,
                'res_id': rec.id,
                'state': 'pending',
                'scheduled_send_datetime': target_send_dt,
            }

            if rec._name == 'account.move':
                queue_entry['move_id'] = rec.id
                queue_entry['invoice_number'] = rec.name

            queue_vals.append(queue_entry)

        if not queue_vals:
            return self.env['whatsapp.message.queue']

        created_queue = self.env['whatsapp.message.queue'].sudo().create(queue_vals)

        if self.dispatch_timing == 'immediate':
            created_queue.process_message_queue(records=created_queue)

        return created_queue

class WhatsappTemplateParam(models.Model):
    _name = 'whatsapp.template.param'
    _description = 'WhatsApp Template Positional Parameter Line'
    _order = 'index asc'

    template_id = fields.Many2one('whatsapp.template', string='Template', required=True, ondelete='cascade')
    index = fields.Integer(string='Parameter Index', required=True, default=1, help="Corresponds to {{1}}, {{2}}, etc.")
    name = fields.Char(string='Description', help="e.g. Patient Name, Amount")
    value_expression = fields.Char(
        string='Jinja Expression',
        required=True,
        help="e.g. {{ object.partner_id.name }}, {{ clinic.name }}, {{ admin_name }}, {{ admin_phone }}"
    )