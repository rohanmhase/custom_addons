from odoo import http
from odoo.http import request
import json
import logging

_logger = logging.getLogger(__name__)

class EngatiWebhookController(http.Controller):
    
    @http.route('/api/engati/delivery_status', type='http', auth='public', methods=['POST'], csrf=False)
    def engati_delivery_status(self, **kw):
        """
        Receives webhook payloads from Engati to update notification status.
        Uses lightweight raw SQL persistence to bypass mail.thread overhead.
        """
        try:
            expected_token = request.env['ir.config_parameter'].sudo().get_param('engati.webhook_token')
            provided_token = request.httprequest.headers.get('Authorization') or kw.get('token')
            
            if provided_token and ' ' in provided_token:
                provided_token = provided_token.split(' ')[-1]
            
            if not expected_token or provided_token != expected_token:
                return request.make_response(
                    json.dumps({'status': 'error', 'message': 'Unauthorized'}), 
                    headers=[('Content-Type', 'application/json')], 
                    status=401
                )

            payload_data = request.httprequest.data
            if not payload_data:
                return request.make_response(json.dumps({'status': 'error', 'message': 'Empty payload'}), headers=[('Content-Type', 'application/json')])
                
            payload = json.loads(payload_data)
            _logger.info("Engati Webhook Payload: %s", payload)

            # Map based on payload structure
            app_id = payload.get('attribute_appointment_id')
            
            # If deeply nested depending on Engati event schema
            if not app_id:
                attrs = payload.get('attributes', {})
                app_id = attrs.get('attribute_appointment_id')
                
            engati_status = payload.get('status', '').lower()
            if not engati_status and 'event' in payload:
                engati_status = payload.get('event', '').lower()

            if not app_id:
                return request.make_response(json.dumps({'status': 'error', 'message': 'Missing attribute_appointment_id'}), headers=[('Content-Type', 'application/json')])

            odoo_status = 'pending'
            if engati_status in ['delivered', 'read', 'success', 'sent', 'message_delivered']:
                odoo_status = 'wa_delivered'
            elif engati_status in ['failed', 'undelivered', 'error', 'message_failed']:
                odoo_status = 'failed'
            else:
                odoo_status = 'queued'

            if odoo_status in ['wa_delivered', 'failed']:
                # Lightweight Persistence Optimization: direct SQL update
                request.env.cr.execute(
                    "UPDATE clinic_schedule_appointment SET notification_status = %s WHERE id = %s",
                    (odoo_status, int(app_id))
                )
                request.env['clinic.schedule.appointment'].invalidate_model(['notification_status'])

            return request.make_response(json.dumps({'status': 'success'}), headers=[('Content-Type', 'application/json')])

        except Exception as e:
            _logger.error("Engati Webhook Error: %s", str(e))
            return request.make_response(json.dumps({'status': 'error', 'message': str(e)}), headers=[('Content-Type', 'application/json')], status=500)
