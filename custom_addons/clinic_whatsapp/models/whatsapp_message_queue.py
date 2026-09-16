import logging
import re
import requests
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class WhatsappMessageQueue(models.Model):
    _name = 'whatsapp.message.queue'
    _description = 'Whatsapp Message Queue'
    _order = 'create_date desc'

    patient_name = fields.Char(string='Recipient Name')
    phone = fields.Char(string='Phone Number', required=True)
    message_body = fields.Text(string='Message Body')

    # Generic record tracking
    res_model = fields.Char(string='Source Model', index=True)
    res_id = fields.Integer(string='Source Record ID', index=True)

    # Legacy fields
    invoice_number = fields.Char(string='Invoice Number')
    invoice_url = fields.Char(string='Invoice URL')
    move_id = fields.Many2one('account.move', string='Invoice', ondelete='set null')

    error_message = fields.Text(string='Error Message')
    state = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('error', 'Error'),
    ], string='Status', default='pending', index=True)
    sent_date = fields.Datetime(string='Sent Date')
    template_id = fields.Many2one('whatsapp.template', string='Template', ondelete='set null', index=True)

    @api.model
    def process_message_queue(self, batch_size=50):
        """Processes pending queue items in isolated batches to prevent worker timeouts and duplicate sends."""
        api_url = (self.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.api_url') or '').strip()
        api_key = (self.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.api_key') or '').strip()

        if not api_key or not api_url:
            _logger.error("WhatsApp API credentials are not configured.")
            return

        headers = {
            "apikey": api_key,
            "Content-Type": "application/json",
        }

        pending_messages = self.search([('state', '=', 'pending')], limit=batch_size)
        if not pending_messages:
            return

        for record in pending_messages:
            # 1. Clean and normalize phone to standard format
            clean_phone = re.sub(r'\D', '', record.phone or '')
            if not clean_phone:
                record.write({'state': 'error', 'error_message': 'Empty or invalid phone number'})
                self.env.cr.commit()
                continue

            if len(clean_phone) == 10:
                formatted_phone = f"91{clean_phone}"
            elif len(clean_phone) == 11 and clean_phone.startswith('0'):
                formatted_phone = f"91{clean_phone[1:]}"
            else:
                formatted_phone = clean_phone

            # 2. Extract message text
            message_text = record.message_body
            if not message_text:
                move = record.move_id
                amount_total = move.amount_total if move else 0.0
                invoice_date = move.invoice_date or fields.Date.today()
                company_name = (move.company_id.name if move and move.company_id else False) or self.env.company.name
                message_text = (
                    f"Dear {record.patient_name or 'Patient'},\n\n"
                    f"This is to confirm your invoice *{record.invoice_number or ''}* for services provided.\n"
                    f"Amount: {amount_total:.2f}\n"
                    f"Date: {invoice_date}\n\n"
                    f"View Bill: {record.invoice_url or ''}\n\n"
                    f"Thank you for choosing {company_name}."
                )

            payload = {
                "number": formatted_phone,
                "text": message_text,
            }

            try:
                response = requests.post(api_url, json=payload, headers=headers, timeout=8)
                if response.status_code in (200, 201):
                    record.write({
                        'state': 'sent',
                        'sent_date': fields.Datetime.now(),
                        'error_message': False,
                    })
                else:
                    record.write({
                        'state': 'error',
                        'error_message': f"HTTP {response.status_code}: {response.text[:500]}",
                    })
            except Exception as exc:
                record.write({
                    'state': 'error',
                    'error_message': str(exc),
                })

            # Commit individual record state so a worker drop does not re-send previous messages
            self.env.cr.commit()

    def action_retry(self):
        """Resets failed queue records back to pending status."""
        records_to_retry = self.filtered(lambda r: r.state == 'error')
        records_to_retry.sudo().write({
            'state': 'pending',
            'error_message': False,
        })
        return True