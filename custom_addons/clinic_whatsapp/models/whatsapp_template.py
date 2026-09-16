import json
import logging
from jinja2.sandbox import SandboxedEnvironment
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class WhatsappTemplate(models.Model):
    _name = 'whatsapp.template'
    _description = 'WhatsApp Message Template'

    name = fields.Char(string='Internal Template Name', required=True)
    smartchat_template_name = fields.Char(
        string='SmartChat Template Slug',
        required=True,
        help="Exact approved template name in Meta/SmartChat (e.g., 'newtemplate')"
    )
    broadcast_name = fields.Char(
        string='Broadcast Campaign Name',
        default='Clinic_Notification',
        help="Tag used in SmartChat campaign tracking"
    )
    model_id = fields.Many2one('ir.model', string='Applies To', required=True, ondelete='cascade')
    model = fields.Char(related='model_id.model', string='Model Name', readonly=True, store=True)

    phone_field = fields.Char(
        string='Phone Number Field',
        required=True,
        default='partner_id.mobile,partner_id.phone',
        help="Comma-separated field paths to evaluate in order (e.g. 'patient_id.phone,partner_id.mobile')"
    )

    # Header Media Configuration
    header_type = fields.Selection([
        ('none', 'None / Text Only'),
        ('media', 'Static Media URL'),
        ('document', 'Dynamic Document Link'),
    ], string='Header Type', default='none', required=True)
    header_media_url = fields.Char(
        string='Header Media URL',
        help="Direct public URL or Jinja expression: {{ base_url }}/..."
    )

    # Dynamic Button URL Configuration
    has_button_url = fields.Boolean(
        string='Has Dynamic Button URL',
        default=False,
        help="Check if this template includes a dynamic website button"
    )
    button_url_suffix = fields.Char(
        string='Button URL Parameter',
        help="Jinja expression for the dynamic button URL suffix (e.g. {{ object.id }} or portal/view/{{ object.id }})"
    )

    # Positional Parameters Line Table
    param_ids = fields.One2many(
        'whatsapp.template.param',
        'template_id',
        string='Template Parameters',
        copy=True
    )

    body = fields.Text(
        string='Message Preview / Fallback',
        help="Informational text preview of the message content."
    )
    active = fields.Boolean(default=True)

    def _resolve_field_value(self, record, field_path):
        """Traverses dot notation safely with comma-separated fallbacks."""
        for candidate_path in (field_path or '').split(','):
            current = record
            for part in candidate_path.strip().split('.'):
                if not current:
                    break
                current = getattr(current, part, False)
            if current:
                return str(current).strip()
        return False

    def send_messages(self, records):
        self.ensure_one()
        if not records:
            return self.env['whatsapp.message.queue']

        env = SandboxedEnvironment()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        queue_vals = []

        # Context helpers for Jinja evaluation
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

        for rec in records:
            # 1. Resolve Recipient Phone
            raw_phone = self._resolve_field_value(rec, self.phone_field)
            if not raw_phone:
                if hasattr(rec, 'partner_id') and rec.partner_id:
                    raw_phone = rec.partner_id.mobile or rec.partner_id.phone
                elif hasattr(rec, 'patient_id') and rec.patient_id:
                    raw_phone = getattr(rec.patient_id, 'mobile', False) or getattr(rec.patient_id, 'phone', False)

            if not raw_phone:
                _logger.warning("No recipient phone found for %s (ID %s)", rec._name, rec.id)
                continue

            # 2. Resolve Recipient Name
            if hasattr(rec, 'patient_id') and rec.patient_id and hasattr(rec.patient_id, 'name'):
                name_str = rec.patient_id.name
            elif hasattr(rec, 'partner_id') and rec.partner_id and hasattr(rec.partner_id, 'name'):
                name_str = rec.partner_id.name
            elif hasattr(rec, 'name'):
                name_str = rec.name
            else:
                name_str = 'Valued Patient'

            # 3. Setup Sandbox Context
            render_context = {
                'object': rec,
                'format_date': format_date,
                'format_datetime': format_datetime,
                'base_url': base_url,
            }

            # 4. Dynamically Render Parameters
            payload_params = {}

            # Evaluate indexed body variables: parameter_value1, parameter_value2, etc.
            for param in self.param_ids.sorted(key=lambda p: p.index):
                try:
                    expr_tmpl = env.from_string(param.value_expression or '')
                    rendered_val = expr_tmpl.render(render_context).strip()
                    payload_params[f"parameter_value{param.index}"] = rendered_val
                except Exception as e:
                    _logger.error("Error evaluating param {{%s}} on template %s: %s", param.index, self.name, str(e))
                    payload_params[f"parameter_value{param.index}"] = ''

            # Evaluate Header Media URL if required
            if self.header_type in ('media', 'document') and self.header_media_url:
                try:
                    url_tmpl = env.from_string(self.header_media_url)
                    payload_params['url'] = url_tmpl.render(render_context).strip()
                except Exception as e:
                    _logger.error("Failed rendering header URL for %s: %s", self.name, str(e))

            # Evaluate Dynamic Button URL if configured
            button_url_val = ''
            if self.has_button_url and self.button_url_suffix:
                try:
                    btn_tmpl = env.from_string(self.button_url_suffix)
                    button_url_val = btn_tmpl.render(render_context).strip()
                    payload_params['button_url'] = button_url_val
                except Exception as e:
                    _logger.error("Failed rendering button URL for %s: %s", self.name, str(e))

            # Render Preview String
            preview_body = ''
            if self.body:
                try:
                    preview_body = env.from_string(self.body).render(render_context)
                except Exception:
                    preview_body = f"Template: {self.smartchat_template_name}"

            queue_entry = {
                'template_id': self.id,
                'patient_name': name_str,
                'phone': str(raw_phone),
                'message_body': preview_body,
                'smartchat_template_name': self.smartchat_template_name,
                'broadcast_name': self.broadcast_name or 'Clinic_Notification',
                'use_button_endpoint': bool(self.has_button_url and button_url_val),
                'params_json': json.dumps(payload_params),
                'res_model': rec._name,
                'res_id': rec.id,
                'state': 'pending',
            }

            if rec._name == 'account.move':
                queue_entry['move_id'] = rec.id
                queue_entry['invoice_number'] = rec.name

            queue_vals.append(queue_entry)

        if queue_vals:
            return self.env['whatsapp.message.queue'].sudo().create(queue_vals)
        return self.env['whatsapp.message.queue']


class WhatsappTemplateParam(models.Model):
    _name = 'whatsapp.template.param'
    _description = 'WhatsApp Dynamic Template Parameter Line'
    _order = 'index asc'

    template_id = fields.Many2one('whatsapp.template', string='Template', required=True, ondelete='cascade')
    index = fields.Integer(string='Parameter Index', required=True, default=1, help="Corresponds to {{1}}, {{2}}, etc.")
    name = fields.Char(string='Description', help="e.g. Patient Name, Timing, Clinic Branch")
    value_expression = fields.Char(
        string='Jinja Expression',
        required=True,
        help="e.g. {{ object.patient_id.name }} or {{ format_datetime(object.start_datetime) }}"
    )