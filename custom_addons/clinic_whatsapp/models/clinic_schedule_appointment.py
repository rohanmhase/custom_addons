import logging
from odoo import models, fields, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ClinicScheduleAppointment(models.Model):
    _inherit = 'clinic.schedule.appointment'

    notification_status = fields.Selection(
        selection_add=[('reschedule_requested', 'Reschedule Requested')],
        ondelete={'reschedule_requested': 'set default'}
    )

    def action_resend_appointment_whatsapp(self):
        """Allows admins to re-dispatch updated appointment details with one click."""
        self.ensure_one()
        template = self.env['whatsapp.template'].sudo().search([
            ('model', '=', self._name),
            ('active', '=', True)
        ], limit=1)
        if not template:
            template = self.env.ref('clinic_whatsapp.whatsapp_template_therapy_comm', raise_if_not_found=False)

        if not template:
            raise UserError(_("No active WhatsApp template found for Clinic Appointments."))

        # Re-dispatch with new slot details
        created_queue = template.sudo().send_messages(self)
        if created_queue:
            self.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                'notification_status': 'wa_delivered'
            })
            self.message_post(body=_(
                "<b>WhatsApp Updated Confirmation Sent:</b> New slot details dispatched to patient."
            ))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Notification Dispatched'),
                    'message': _('Updated appointment details sent to patient via WhatsApp.'),
                    'type': 'success',
                }
            }
        return False