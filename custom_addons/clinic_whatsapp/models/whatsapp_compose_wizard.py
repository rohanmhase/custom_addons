from odoo import models, fields, api, _
from odoo.exceptions import UserError
import requests
import re


class WhatsAppComposeWizard(models.TransientModel):
    _name = 'whatsapp.compose.wizard'
    _description = 'Direct 1-on-1 WhatsApp Messenger'

    recipient_name = fields.Char(string='Patient Name', required=True)
    recipient_phone = fields.Char(string='Recipient Phone', required=True)
    sender_phone_id = fields.Many2one(
        'whatsapp.sender.phone',
        string='Sender Line',
        required=True,
        default=lambda self: self.env['whatsapp.sender.phone'].search([('is_default', '=', True)], limit=1)
    )
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic Branch')
    message_body = fields.Text(string='Message', required=True)

    # Context references
    reply_id = fields.Many2one('whatsapp.message.reply', string='Replying to')
    res_model = fields.Char(string='Source Model')
    res_id = fields.Integer(string='Source ID')

    def action_send_direct_message(self):
        self.ensure_one()
        ICP = self.env['ir.config_parameter'].sudo()
        api_url = (ICP.get_param('clinic_whatsapp.api_url') or 'https://graph.facebook.com/v26.0').rstrip('/')
        token = (ICP.get_param('clinic_whatsapp.api_key') or '').strip()

        clean_digits = re.sub(r'\D', '', self.recipient_phone or '')
        formatted_phone = f"91{clean_digits[-10:]}" if len(clean_digits) >= 10 else clean_digits

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": formatted_phone,
            "type": "text",
            "text": {"body": self.message_body}
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        phone_number_id = self.sender_phone_id.phone_number_id or '1395062320356609'
        res = requests.post(f"{api_url}/{phone_number_id}/messages", json=payload, headers=headers, timeout=10)

        if res.status_code not in (200, 201):
            raise UserError(_("Failed to send WhatsApp message via Meta API: %s") % res.text)

        wamid = res.json().get('messages', [{}])[0].get('id')

        # Log into WhatsApp Message Queue for full audit trail
        self.env['whatsapp.message.queue'].create({
            'patient_name': self.recipient_name,
            'phone': formatted_phone,
            'phone_number_id': phone_number_id,
            'clinic_id': self.clinic_id.id if self.clinic_id else False,
            'wamid': wamid,
            'state': 'sent',
            'res_model': self.res_model,
            'res_id': self.res_id,
        })

        # Post to source document chatter (e.g. Appointment)
        if self.res_model and self.res_id:
            record = self.env[self.res_model].browse(self.res_id)
            if record.exists():
                record.message_post(body=(
                    f"<b><i class='fa fa-whatsapp text-success'></i> WhatsApp Sent by {self.env.user.name}:</b><br/>"
                    f"<p>{self.message_body}</p>"
                ))

        return {'type': 'ir.actions.act_window_close'}