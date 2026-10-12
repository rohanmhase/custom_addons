import json
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppWebhookController(http.Controller):

  @http.route(
      '/whatsapp/webhook',
      type='http',
      auth='public',
      methods=['GET'],
      csrf=False,
  )
  def verify_webhook(self, **kwargs):
    """Handles Meta webhook verification handshake."""
    mode = kwargs.get('hub.mode') or request.params.get('hub.mode')
    token = kwargs.get('hub.verify_token') or request.params.get(
        'hub.verify_token'
    )
    challenge = kwargs.get('hub.challenge') or request.params.get(
        'hub.challenge'
    )

    configured_token = (
        request.env['ir.config_parameter']
        .sudo()
        .get_param('clinic_whatsapp.verify_token')
        or 'clinic_wa_verify_2026'
    ).strip()

    if mode == 'subscribe' and token == configured_token:
      return request.make_response(
          str(challenge),
          headers=[('Content-Type', 'text/plain; charset=utf-8')],
      )

    _logger.warning(
        'Webhook verification failed: token mismatch (received %s,'
        ' expected %s)',
        token,
        configured_token,
    )
    return request.make_response(
        'Forbidden',
        headers=[('Content-Type', 'text/plain; charset=utf-8')],
        status=403,
    )

  @http.route(
      '/whatsapp/webhook',
      type='json',
      auth='public',
      methods=['POST'],
      csrf=False,
  )
  def receive_webhook(self):
    """Processes incoming status updates and button choices."""
    try:
      data = json.loads(request.httprequest.data.decode('utf-8'))
    except Exception:
      data = request.params or {}

    # Handle statuses and messages
    for entry in data.get('entry', []):
      for change in entry.get('changes', []):
        val = change.get('value', {})
        # Status delivery update
        for status_obj in val.get('statuses', []):
          wamid = status_obj.get('id')
          status_str = status_obj.get('status')
          if wamid and status_str in ('delivered', 'read'):
            q = (
                request.env['whatsapp.message.queue']
                .sudo()
                .search([('wamid', '=', wamid)], limit=1)
            )
            if q:
              q.write({'state': status_str})

        # Inbound messages / Quick replies
        for msg in val.get('messages', []):
          from_phone = msg.get('from')
          msg_id = msg.get('id')
          context_wamid = msg.get('context', {}).get('id')
          chosen_option = (
              msg.get('button', {}).get('text')
              or msg.get('interactive', {})
              .get('button_reply', {})
              .get('title')
              or msg.get('text', {}).get('body', '')
          )

          matched_queue = (
              request.env['whatsapp.message.queue']
              .sudo()
              .search([('wamid', '=', context_wamid)], limit=1)
              if context_wamid
              else False
          )

          request.env['whatsapp.message.reply'].sudo().create({
              'patient_name': (
                  matched_queue.patient_name if matched_queue else 'Patient'
              ),
              'phone': from_phone,
              'wamid': msg_id,
              'context_wamid': context_wamid,
              'selected_option': chosen_option,
              'message_body': chosen_option,
              'queue_id': matched_queue.id if matched_queue else False,
              'res_model': (
                  matched_queue.res_model if matched_queue else False
              ),
              'res_id': matched_queue.res_id if matched_queue else False,
          })

          # Chatter audit
          if matched_queue and matched_queue.res_model:
            doc = (
                request.env[matched_queue.res_model]
                .sudo()
                .browse(matched_queue.res_id)
            )
            if doc.exists() and hasattr(doc, 'message_post'):
              doc.message_post(
                  body=(
                      f"<b>WhatsApp Reply:</b> <span class='badge"
                      f" text-bg-success'>{chosen_option}</span> from"
                      f' {from_phone}'
                  )
              )

    return {'status': 'success'}