import json
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
    message_body = fields.Text(string='Message Preview')

    # SmartChat Dynamic Fields
    smartchat_template_name = fields.Char(string='SmartChat Template Name')
    broadcast_name = fields.Char(string='Broadcast Name', default='Clinic_Notification')
    use_button_endpoint = fields.Boolean(string='Use Dynamic Button Endpoint', default=False)
    params_json = fields.Text(string='Dynamic Parameters (JSON)')
    wamid = fields.Char(string='Meta Message ID (wamid)', readonly=True, index=True)

    # Generic Record Tracking
    res_model = fields.Char(string='Source Model', index=True)
    res_id = fields.Integer(string='Source Record ID', index=True)

    # Legacy / Invoice Compatibility
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
        """Dispatches queued messages dynamically to SmartChat API."""
        raw_api_url = (self.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.api_url') or
                       'https://smartchatapi.live/portal/Api').strip().rstrip('/')
        token = (self.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.api_key') or '').strip()

        if not token:
            _logger.error("SmartChat API Token is not configured in Settings.")
            return

        base_api = re.sub(r'/(send_template_message.*)$', '', raw_api_url)

        pending_messages = self.search([('state', '=', 'pending')], limit=batch_size)
        if not pending_messages:
            return

        for record in pending_messages:
            clean_phone = re.sub(r'\D', '', record.phone or '')
            if not clean_phone:
                record.write({'state': 'error', 'error_message': 'Missing or invalid phone number'})
                self.env.cr.commit()
                continue

            if len(clean_phone) == 10:
                formatted_phone = f"91{clean_phone}"
            elif len(clean_phone) == 11 and clean_phone.startswith('0'):
                formatted_phone = f"91{clean_phone[1:]}"
            else:
                formatted_phone = clean_phone

            # 2. Select Appropriate SmartChat Endpoint
            if record.use_button_endpoint:
                endpoint = f"{base_api}/send_template_message_using_url"
            else:
                endpoint = f"{base_api}/send_template_message"

            # 3. Assemble Dynamic Query Parameters (Always include 'url' fallback)
            query_params = {
                'sender_whatsapp_number': formatted_phone,
                'token': token,
                'template_name': record.smartchat_template_name or 'therapy_comm',
                'broadcast_name': record.broadcast_name or 'Clinic_Notification',
                'url': '',
            }

            if record.params_json:
                try:
                    dynamic_params = json.loads(record.params_json)
                    query_params.update(dynamic_params)
                except Exception as parse_err:
                    _logger.error("Failed to parse params_json for queue ID %s: %s", record.id, str(parse_err))

                # 4. Dispatch via GET (SmartChat expects query-based URL parameters)
            try:
                response = requests.get(endpoint, params=query_params, timeout=12)
                resp_json = response.json() if response.content else {}

                is_success = response.status_code == 200 and str(resp_json.get('status')) == '200'

                if is_success:
                    wamid = resp_json.get('message_id') or resp_json.get('request_id')
                    record.write({
                        'state': 'sent',
                        'wamid': wamid,
                        'sent_date': fields.Datetime.now(),
                        'error_message': False,
                    })

                    if record.res_model == 'clinic.schedule.appointment' and record.res_id:
                        appointment = self.env['clinic.schedule.appointment'].browse(record.res_id)
                        if appointment.exists():
                            appointment.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                                'notification_status': 'wa_delivered'
                            })
                else:
                    err_detail = (
                            resp_json.get('messsage') or
                            resp_json.get('message') or
                            resp_json.get('error') or
                            resp_json.get('msg') or
                            f"HTTP {response.status_code}: {response.text[:300]}"
                    )
                    record.write({
                        'state': 'error',
                        'error_message': f"SmartChat Error: {err_detail}",
                    })
                    if record.res_model == 'clinic.schedule.appointment' and record.res_id:
                        appointment = self.env['clinic.schedule.appointment'].browse(record.res_id)
                        if appointment.exists():
                            appointment.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                                'notification_status': 'failed'
                            })

            except Exception as exc:
                record.write({
                    'state': 'error',
                    'error_message': f"Connection Error: {str(exc)}",
                })
                if record.res_model == 'clinic.schedule.appointment' and record.res_id:
                    app = self.env['clinic.schedule.appointment'].browse(record.res_id)
                    if app.exists():
                        app.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                            'notification_status': 'failed'
                        })

            self.env.cr.commit()

    def action_retry(self):
        """Resets failed queue records back to pending status."""
        records_to_retry = self.filtered(lambda r: r.state == 'error')
        records_to_retry.sudo().write({
            'state': 'pending',
            'error_message': False,
        })
        return True