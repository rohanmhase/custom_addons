import json
import logging
import re
import requests
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


def _normalize_phone(phone_str):
    """Extracts last 10 digits for matching Indian phone numbers."""
    digits = re.sub(r'\D', '', phone_str or '')
    return digits[-10:] if len(digits) >= 10 else digits


def _send_whatsapp_session_text(env, phone_number_id, to_phone, text_message):
    """Sends a free-form text message within the 24-hr service window via Meta Cloud API."""
    ICP = env['ir.config_parameter'].sudo()
    api_url = (ICP.get_param('clinic_whatsapp.api_url') or 'https://graph.facebook.com/v26.0').rstrip('/')
    token = (ICP.get_param('clinic_whatsapp.api_key') or '').strip()

    if not token or not phone_number_id:
        _logger.warning("WhatsApp session send aborted: missing token or sender phone_number_id")
        return False

    clean_digits = re.sub(r'\D', '', to_phone or '')
    formatted_phone = f"91{clean_digits[-10:]}" if len(clean_digits) >= 10 else clean_digits

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": formatted_phone,
        "type": "text",
        "text": {"body": text_message}
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        res = requests.post(f"{api_url}/{phone_number_id}/messages", json=payload, headers=headers, timeout=10)
        return res.status_code in (200, 201)
    except Exception as e:
        _logger.exception("Failed session text dispatch: %s", str(e))
        return False


class WhatsAppWebhookController(http.Controller):

    @http.route('/whatsapp/webhook', type='http', auth='public', methods=['GET'], csrf=False)
    def verify_webhook(self, **kwargs):
        mode = kwargs.get('hub.mode') or request.params.get('hub.mode')
        token = kwargs.get('hub.verify_token') or request.params.get('hub.verify_token')
        challenge = kwargs.get('hub.challenge') or request.params.get('hub.challenge')

        configured_token = (
                request.env['ir.config_parameter'].sudo().get_param('clinic_whatsapp.verify_token')
                or 'clinic_wa_verify_2026'
        ).strip()

        if mode == 'subscribe' and token == configured_token:
            return request.make_response(str(challenge), headers=[('Content-Type', 'text/plain; charset=utf-8')])

        return request.make_response("Forbidden", headers=[('Content-Type', 'text/plain; charset=utf-8')], status=403)

    @http.route('/whatsapp/webhook', type='json', auth='public', methods=['POST'], csrf=False)
    def receive_webhook(self):
        try:
            data = json.loads(request.httprequest.data.decode('utf-8'))
        except Exception:
            data = request.params or {}

        env = request.env

        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                val = change.get('value', {})
                sender_phone_id = val.get('metadata', {}).get('phone_number_id')

                # 1. Delivery & Read Receipts
                for status_obj in val.get('statuses', []):
                    wamid = status_obj.get('id')
                    status_str = status_obj.get('status')
                    if wamid and status_str in ('delivered', 'read'):
                        q = env['whatsapp.message.queue'].sudo().search([('wamid', '=', wamid)], limit=1)
                        if q:
                            q.write({'state': status_str})

                # 2. Inbound Messages Processing
                for msg in val.get('messages', []):
                    from_phone = msg.get('from', '')
                    raw_msg_id = msg.get('id')
                    context_wamid = msg.get('context', {}).get('id')
                    msg_type = msg.get('type', 'text')
                    clean_phone_10 = _normalize_phone(from_phone)

                    # Classify message type accurately
                    if msg_type == 'button':
                        chosen_option = msg.get('button', {}).get('text', '').strip()
                        reply_type = 'quick_reply'
                    elif msg_type == 'interactive':
                        chosen_option = (
                                msg.get('interactive', {}).get('button_reply', {}).get('title')
                                or msg.get('interactive', {}).get('list_reply', {}).get('title')
                                or ''
                        ).strip()
                        reply_type = 'quick_reply'
                    else:
                        chosen_option = msg.get('text', {}).get('body', '').strip()
                        reply_type = 'text'

                    # Match Queue Record (if button click replying to our message)
                    matched_queue = env['whatsapp.message.queue'].sudo().search(
                        [('wamid', '=', context_wamid)], limit=1
                    ) if context_wamid else False

                    # Intelligent Patient & Clinic Resolution
                    patient_name = False
                    clinic_obj = False
                    appointment_obj = False

                    if matched_queue:
                        patient_name = matched_queue.patient_name
                        clinic_obj = matched_queue.clinic_id
                        if matched_queue.res_model == 'clinic.schedule.appointment' and matched_queue.res_id:
                            appointment_obj = env['clinic.schedule.appointment'].sudo().browse(matched_queue.res_id)

                    # Fallback Resolution: Search recent appointment or contact by phone
                    if not patient_name or patient_name == 'Patient' or not clinic_obj:
                        recent_app = env['clinic.schedule.appointment'].sudo().search([
                            '|', ('patient_id.mobile', 'ilike', clean_phone_10),
                            ('patient_id.phone', 'ilike', clean_phone_10)
                        ], order='id desc', limit=1)

                        if recent_app:
                            appointment_obj = recent_app
                            patient_name = recent_app.patient_id.name
                            clinic_obj = recent_app.clinic_id
                        else:
                            partner = env['res.partner'].sudo().search([
                                '|', ('mobile', 'ilike', clean_phone_10),
                                ('phone', 'ilike', clean_phone_10)
                            ], limit=1)
                            if partner:
                                patient_name = partner.name

                    patient_name = patient_name or 'Patient'
                    dispatch_phone_id = sender_phone_id or (
                        matched_queue.phone_number_id if matched_queue else '1395062320356609')
                    helpline_str = clinic_obj.get_helpline_display() if clinic_obj else '9082777624'
                    clinic_title = clinic_obj.name if clinic_obj else 'ResearchAyu Knee Clinics'

                    # -------------------------------------------------------------
                    # 1. ANTI-FLIP-FLOP GUARD: Check for Prior Button Selection
                    # -------------------------------------------------------------
                    if reply_type == 'quick_reply' and context_wamid:
                        prior_choice = env['whatsapp.message.reply'].sudo().search([
                            ('context_wamid', '=', context_wamid),
                            ('reply_type', '=', 'quick_reply')
                        ], order='id asc', limit=1)

                        if prior_choice:
                            locked_answer = prior_choice.selected_option
                            _logger.info(
                                "Flip-flop blocked for %s. Previous: '%s', Attempted: '%s'",
                                from_phone, locked_answer, chosen_option
                            )

                            if appointment_obj and appointment_obj.exists():
                                appointment_obj.message_post(body=(
                                    f"<b><i class='fa fa-ban text-warning'></i> WhatsApp Selection Blocked:</b> "
                                    f"Patient attempted to select <code>{chosen_option}</code>, but initial choice "
                                    f"<code>{locked_answer}</code> is locked."
                                ))

                            # Locked warning without any helpline number
                            lock_msg = (
                                f"Dear {patient_name}, you have already selected \"{locked_answer}\" for this session. "
                                f"Our clinic team will reach out to you directly if any further assistance is required."
                            )
                            _send_whatsapp_session_text(env, dispatch_phone_id, from_phone, lock_msg)
                            continue

                    # -------------------------------------------------------------
                    # 2. Record Inbound Message
                    # -------------------------------------------------------------
                    env['whatsapp.message.reply'].sudo().create({
                        'patient_name': patient_name,
                        'phone': from_phone,
                        'wamid': raw_msg_id,
                        'context_wamid': context_wamid,
                        'reply_type': reply_type,
                        'selected_option': chosen_option,
                        'message_body': chosen_option,
                        'queue_id': matched_queue.id if matched_queue else False,
                        'clinic_id': clinic_obj.id if clinic_obj else False,
                        'res_model': 'clinic.schedule.appointment' if appointment_obj else (
                            matched_queue.res_model if matched_queue else False),
                        'res_id': appointment_obj.id if appointment_obj else (
                            matched_queue.res_id if matched_queue else False),
                    })

                    # -------------------------------------------------------------
                    # 3. Action Logic & Automated Session Replies (No Helpline)
                    # -------------------------------------------------------------
                    lower_choice = chosen_option.lower()

                    if reply_type == 'quick_reply' and 'confirm' in lower_choice:
                        if appointment_obj and appointment_obj.exists():
                            appointment_obj.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write(
                                {
                                    'notification_status': 'wa_delivered'
                                })
                            appointment_obj.message_post(body=(
                                f"<b><i class='fa fa-check-circle text-success'></i> Confirmed via WhatsApp</b> by {patient_name}."
                            ))

                        confirm_reply = (
                            f"Thank you for confirming, {patient_name}! "
                            f"Please arrive at {clinic_title} 15 minutes prior to your scheduled slot."
                        )
                        _send_whatsapp_session_text(env, dispatch_phone_id, from_phone, confirm_reply)

                    elif reply_type == 'quick_reply' and 'reschedule' in lower_choice:
                        if appointment_obj and appointment_obj.exists():
                            # Keep slot locked and intact on the matrix grid; only flag the notification state
                            appointment_obj.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write(
                                {
                                    'notification_status': 'reschedule_requested'
                                })
                            appointment_obj.message_post(body=(
                                f"<b><i class='fa fa-calendar-times-o text-danger'></i> Reschedule Requested via WhatsApp</b> by {patient_name}. "
                                f"Existing slot remains held until reassigned."
                            ))

                        reschedule_reply = (
                            f"Dear {patient_name}, we have received your request to reschedule your appointment at {clinic_title}.\n\n"
                            f"Someone from the clinic will contact you shortly to coordinate a convenient time. "
                            f"Please note that until a new slot is confirmed, your current appointment slot remains confirmed and reserved for you."
                        )
                        _send_whatsapp_session_text(env, dispatch_phone_id, from_phone, reschedule_reply)

                    else:
                        # Free-form patient text
                        if appointment_obj and appointment_obj.exists():
                            appointment_obj.message_post(
                                body=f"<b>WhatsApp Message from {patient_name}:</b> {chosen_option}")

                        default_reply = (
                            f"Hello {patient_name}, thank you for contacting {clinic_title}. "
                            f"We have received your message: \"{chosen_option}\". "
                            f"Our clinic team will follow up with you shortly."
                        )
                        _send_whatsapp_session_text(env, dispatch_phone_id, from_phone, default_reply)

        return {'status': 'success'}