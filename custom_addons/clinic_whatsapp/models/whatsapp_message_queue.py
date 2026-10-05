import json
import logging
import re
import requests
from odoo import models, fields, api, _

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
    response_json = fields.Text(string='Gateway Response JSON', readonly=True)
    wamid = fields.Char(string='Meta Message ID (wamid)', readonly=True, index=True)

    # Time-Aware Dispatching
    scheduled_send_datetime = fields.Datetime(
        string='Scheduled Send Time',
        default=fields.Datetime.now,
        index=True,
        help="Message will not be dispatched before this timestamp."
    )

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
    def process_message_queue(self, batch_size=50, records=None):
        """Dispatches queued messages to SmartChat API."""
        raw_api_url = (self.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.api_url') or
                       'https://smartchatapi.live/portal/Api').strip().rstrip('/')
        token = (self.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.api_key') or '').strip()

        if not token:
            _logger.error("SmartChat API Token is not configured in Settings.")
            return

        base_api = re.sub(r'/(send_template_message.*)$', '', raw_api_url)

        # 1. Determine targets: specific records (immediate) vs. time-aware batch (cron)
        if records is not None:
            pending_messages = records.filtered(lambda r: r.state == 'pending')
        else:
            pending_messages = self.search([
                ('state', '=', 'pending'),
                ('scheduled_send_datetime', '<=', fields.Datetime.now())
            ], limit=batch_size)

        if not pending_messages:
            return

        # SmartChat v2.37.1 Header specifications
        headers = {
            'token': token,
            'Authorization': f'Bearer {token}',
        }

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
                response = requests.get(endpoint, params=query_params, timeout=15)
                raw_response_text = response.text
                resp_json = response.json() if response.content else {}

                is_success = (response.status_code in (200, 201)) and (
                        str(resp_json.get('status')).lower() in ('200', 'processing', 'success', 'true') or
                        resp_json.get('result') is True or
                        str(resp_json.get('result')).lower() == 'true' or
                        bool(resp_json.get('message_id')) or
                        bool(resp_json.get('messages'))
                )

                if is_success:
                    msg_list = resp_json.get('messages')
                    nested_id = msg_list[0].get('id') if isinstance(msg_list, list) and msg_list else False
                    wamid = resp_json.get('message_id') or resp_json.get('request_id') or nested_id or resp_json.get(
                        'id')

                    record.write({
                        'state': 'sent',
                        'wamid': wamid,
                        'sent_date': fields.Datetime.now(),
                        'response_json': raw_response_text,
                        'error_message': False,
                    })

                    # Sync status if linked to Appointment Matrix
                    if record.res_model == 'clinic.schedule.appointment' and record.res_id:
                        appointment = self.env['clinic.schedule.appointment'].browse(record.res_id)
                        if appointment.exists():
                            appointment.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                                'notification_status': 'wa_delivered'
                            })

                    # Chatter Audit Logging: Post confirmation on the source document
                    if record.res_model and record.res_id:
                        try:
                            source_doc = self.env[record.res_model].browse(record.res_id)
                            if source_doc.exists() and hasattr(source_doc, 'message_post'):
                                source_doc.message_post(body=_(
                                    "<b><i class='fa fa-whatsapp text-success'></i> WhatsApp Sent:</b> "
                                    "Template <code>%s</code> dispatched.<br/>"
                                    "<b>Recipient:</b> %s (%s)<br/>"
                                    "<b>Meta UID:</b> <code>%s</code>"
                                ) % (
                                                                 record.smartchat_template_name or 'Template',
                                                                 record.patient_name or 'Patient',
                                                                 formatted_phone,
                                                                 wamid or 'Pending'
                                                             ))
                        except Exception as chatter_err:
                            _logger.warning("Chatter log error on %s (ID %s): %s", record.res_model, record.res_id,
                                            str(chatter_err))

                else:
                    err_detail = (
                            resp_json.get('messsage') or
                            resp_json.get('message') or
                            resp_json.get('error') or
                            resp_json.get('msg') or
                            f"HTTP {response.status_code}: {raw_response_text[:300]}"
                    )
                    record.write({
                        'state': 'error',
                        'response_json': raw_response_text,
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

            self.env.cr.commit()

    def action_retry(self):
        """Resets failed queue records back to pending status and re-processes immediately."""
        records_to_retry = self.filtered(lambda r: r.state == 'error')
        records_to_retry.sudo().write({
            'state': 'pending',
            'error_message': False,
            'scheduled_send_datetime': fields.Datetime.now(),
        })
        self.process_message_queue(records=records_to_retry)
        return True