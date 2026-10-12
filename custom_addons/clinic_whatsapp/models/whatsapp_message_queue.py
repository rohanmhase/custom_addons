import json
import logging
import re
from odoo import _, api, fields, models
import requests

_logger = logging.getLogger(__name__)


class WhatsappMessageQueue(models.Model):
  _name = 'whatsapp.message.queue'
  _description = 'Meta WhatsApp Message Queue'
  _order = 'create_date desc'

  patient_name = fields.Char(string='Recipient Name')
  phone = fields.Char(string='Phone Number', required=True)
  message_body = fields.Text(string='Message Preview')

  meta_template_name = fields.Char(
      string='Meta Template', required=True, default='patient_invoice_receipt'
  )
  language_code = fields.Char(string='Language', default='en')
  phone_number_id = fields.Char(
      string='Sender Phone ID',
      help='Meta Phone Number ID used for dispatching this message',
  )
  clinic_id = fields.Many2one(
      'clinic.clinic', string='Clinic Branch', ondelete='set null'
  )

  params_json = fields.Text(string='Evaluated Parameters (JSON)')
  response_json = fields.Text(string='Gateway Response JSON', readonly=True)
  wamid = fields.Char(
      string='Meta Message ID (wamid)', readonly=True, index=True
  )

  scheduled_send_datetime = fields.Datetime(
      string='Scheduled Send Time',
      default=fields.Datetime.now,
      index=True,
  )

  res_model = fields.Char(string='Source Model', index=True)
  res_id = fields.Integer(string='Source Record ID', index=True)
  move_id = fields.Many2one(
      'account.move', string='Invoice', ondelete='set null'
  )
  invoice_number = fields.Char(string='Invoice Reference')

  error_message = fields.Text(string='Error Details')
  state = fields.Selection(
      [
          ('pending', 'Pending'),
          ('sent', 'Sent'),
          ('delivered', 'Delivered'),
          ('read', 'Read'),
          ('error', 'Error'),
      ],
      string='Status',
      default='pending',
      index=True,
  )
  sent_date = fields.Datetime(string='Sent Date')
  template_id = fields.Many2one(
      'whatsapp.template', string='Template', ondelete='set null'
  )

  # Replies linked to this outbound message
  reply_ids = fields.One2many(
      'whatsapp.message.reply',
      'queue_id',
      string='Inbound Patient Responses',
  )

  @api.model
  def process_message_queue(self, batch_size=50, records=None):
    ICP = self.env['ir.config_parameter'].sudo()
    api_url = (
        ICP.get_param('clinic_whatsapp.api_url')
        or 'https://graph.facebook.com/v25.0'
    ).rstrip('/')
    token = (ICP.get_param('clinic_whatsapp.api_key') or '').strip()
    global_phone_id = (
        ICP.get_param('clinic_whatsapp.phone_number_id') or '1395062320356609'
    ).strip()

    if not token:
      _logger.error('Meta WhatsApp Access Token is missing in Settings.')
      return

    if records is not None:
      pending_messages = records.filtered(lambda r: r.state == 'pending')
    else:
      pending_messages = self.search(
          [
              ('state', '=', 'pending'),
              ('scheduled_send_datetime', '<=', fields.Datetime.now()),
          ],
          limit=batch_size,
      )

    if not pending_messages:
      return

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    for record in pending_messages:
      clean_digits = re.sub(r'\D', '', record.phone or '')
      if not clean_digits:
        record.write(
            {'state': 'error', 'error_message': 'Missing or empty phone number'}
        )
        self.env.cr.commit()
        continue

      if len(clean_digits) == 10:
        formatted_phone = f'91{clean_digits}'
      elif len(clean_digits) == 11 and clean_digits.startswith('0'):
        formatted_phone = f'91{clean_digits[1:]}'
      else:
        formatted_phone = clean_digits

      sender_id = record.phone_number_id or global_phone_id
      endpoint = f'{api_url}/{sender_id}/messages'

      body_parameters_list = []
      header_document_dict = None
      if record.params_json:
        try:
          parsed_data = json.loads(record.params_json)
          body_parameters_list = parsed_data.get('body_params', [])
          header_document_dict = parsed_data.get('header_document', None)
        except Exception as parse_err:
          _logger.error(
              'Failed parsing JSON parameters for queue #%s: %s',
              record.id,
              str(parse_err),
          )

      components = []

      if header_document_dict and header_document_dict.get('url'):
        components.append({
            'type': 'header',
            'parameters': [{
                'type': 'document',
                'document': {
                    'link': header_document_dict['url'],
                    'filename': header_document_dict.get(
                        'filename', 'Invoice.pdf'
                    ),
                },
            }],
        })

      if body_parameters_list:
        components.append({
            'type': 'body',
            'parameters': [
                {'type': 'text', 'text': str(val)}
                for val in body_parameters_list
            ],
        })

      payload = {
          'messaging_product': 'whatsapp',
          'recipient_type': 'individual',
          'to': formatted_phone,
          'type': 'template',
          'template': {
              'name': record.meta_template_name,
              'language': {'code': record.language_code or 'en'},
              'components': components,
          },
      }

      try:
        response = requests.post(
            endpoint, json=payload, headers=headers, timeout=15
        )
        resp_json = response.json() if response.content else {}

        if response.status_code in (200, 201) and resp_json.get('messages'):
          wamid = resp_json['messages'][0].get('id')
          record.write({
              'state': 'sent',
              'wamid': wamid,
              'sent_date': fields.Datetime.now(),
              'response_json': response.text,
              'error_message': False,
          })

          if (
              record.res_model == 'clinic.schedule.appointment'
              and record.res_id
          ):
            app = self.env['clinic.schedule.appointment'].browse(record.res_id)
            if app.exists():
              app.with_context(
                  bypass_matrix_lock=True, bypass_notification_reset=True
              ).write({'notification_status': 'wa_delivered'})

          if record.res_model and record.res_id:
            try:
              source_doc = self.env[record.res_model].browse(record.res_id)
              if source_doc.exists() and hasattr(source_doc, 'message_post'):
                source_doc.message_post(
                    body=_(
                        "<b><i class='fa fa-whatsapp text-success'></i>"
                        ' WhatsApp Dispatched:</b> <code>%s</code> to %s'
                        ' (%s)<br/><b>Meta UID:</b> <code>%s</code>'
                    )
                    % (
                        record.meta_template_name,
                        record.patient_name or 'Patient',
                        formatted_phone,
                        wamid or '',
                    )
                )
            except Exception as chatter_err:
              _logger.warning(
                  'Chatter logging error: %s',
                  str(chatter_err),
              )

        else:
          err_msg = resp_json.get('error', {}).get(
              'message', response.text[:300]
          )
          record.write({
              'state': 'error',
              'response_json': response.text,
              'error_message': f'Meta Graph API Error: {err_msg}',
          })

      except Exception as exc:
        record.write({
            'state': 'error',
            'error_message': f'Connection Error: {str(exc)}',
        })

      self.env.cr.commit()

  def action_retry(self):
    records_to_retry = self.filtered(lambda r: r.state == 'error')
    records_to_retry.sudo().write({
        'state': 'pending',
        'error_message': False,
        'scheduled_send_datetime': fields.Datetime.now(),
    })
    self.process_message_queue(records=records_to_retry)
    return True