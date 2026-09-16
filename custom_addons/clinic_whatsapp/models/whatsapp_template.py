import logging
from jinja2.sandbox import SandboxedEnvironment
from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class WhatsappTemplate(models.Model):
    _name = 'whatsapp.template'
    _description = 'WhatsApp Message Template'

    name = fields.Char(string='Template Name', required=True)
    model_id = fields.Many2one('ir.model', string='Applies To', required=True, ondelete='cascade')
    model = fields.Char(related='model_id.model', string='Model Name', readonly=True, store=True)
    phone_field = fields.Char(
        string='Phone Number Field',
        required=True,
        default='partner_id.mobile,partner_id.phone',
        help="Comma-separated field paths to try in order (e.g. 'partner_id.mobile,partner_id.phone' or 'patient_id.phone')"
    )
    body = fields.Text(
        string='Message Body',
        required=True,
        help="Write your template using {{ object.field_name }} placeholders."
    )
    active = fields.Boolean(default=True)

    def _resolve_field_value(self, record, field_path):
        """Traverses dot notation safely. Supports comma-separated fallback paths."""
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
        jinja_template = env.from_string(self.body or '')
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        queue_vals = []

        for rec in records:
            # 1. Resolve recipient phone with automatic fallbacks
            raw_phone = self._resolve_field_value(rec, self.phone_field)
            if not raw_phone:
                if hasattr(rec, 'partner_id') and rec.partner_id:
                    raw_phone = rec.partner_id.mobile or rec.partner_id.phone
                elif hasattr(rec, 'patient_id') and rec.patient_id:
                    raw_phone = getattr(rec.patient_id, 'mobile', False) or getattr(rec.patient_id, 'phone', False)

            if not raw_phone:
                _logger.warning("No phone found for %s (id: %s) via '%s'", rec._name, rec.id, self.phone_field)
                continue

            # 2. Resolve recipient name
            if hasattr(rec, 'patient_id') and rec.patient_id and hasattr(rec.patient_id, 'name'):
                name_str = rec.patient_id.name
            elif hasattr(rec, 'partner_id') and rec.partner_id and hasattr(rec.partner_id, 'name'):
                name_str = rec.partner_id.name
            elif hasattr(rec, 'name'):
                name_str = rec.name
            else:
                name_str = 'Valued Patient'

            # 3. Contextual formatting helpers (converts UTC to user timezone)
            def format_datetime(dt, fmt='%d %b %Y, %I:%M %p'):
                if not dt:
                    return ''
                if isinstance(dt, str):
                    dt = fields.Datetime.from_string(dt)
                return fields.Datetime.context_timestamp(rec, dt).strftime(fmt)

            def format_date(d, fmt='%d %b %Y'):
                if not d:
                    return ''
                if isinstance(d, str):
                    d = fields.Date.from_string(d)
                return d.strftime(fmt)

            # 4. Render Jinja template
            try:
                rendered_body = jinja_template.render({
                    'object': rec,
                    'format_date': format_date,
                    'format_datetime': format_datetime,
                    'base_url': base_url,
                })
            except Exception as e:
                _logger.error("Failed rendering template %s for %s id %s: %s", self.name, rec._name, rec.id, str(e))
                continue

            vals = {
                'template_id': self.id,
                'patient_name': name_str,
                'phone': str(raw_phone),
                'message_body': rendered_body,
                'res_model': rec._name,
                'res_id': rec.id,
                'state': 'pending',
            }

            if rec._name == 'account.move':
                vals['move_id'] = rec.id
                vals['invoice_number'] = rec.name
                if hasattr(rec, 'access_token') and rec.access_token:
                    vals['invoice_url'] = f"{base_url}/mail/view?model=account.move&res_id={rec.id}&access_token={rec.access_token}"

            queue_vals.append(vals)

        if queue_vals:
            return self.env['whatsapp.message.queue'].sudo().create(queue_vals)
        return self.env['whatsapp.message.queue']