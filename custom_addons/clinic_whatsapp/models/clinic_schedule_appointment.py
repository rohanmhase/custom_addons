import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ClinicScheduleAppointment(models.Model):
    _inherit = 'clinic.schedule.appointment'

    notification_status = fields.Selection(
        selection_add=[
            ('reschedule_requested', 'Reschedule Requested'),
            ('rescheduled_done', 'Successfully Rescheduled'),
        ],
        ondelete={'reschedule_requested': 'set default', 'rescheduled_done': 'set default'}
    )

    # Dedicated section & KPI tracking field
    wa_response_status = fields.Selection([
        ('confirmed', 'Confirmed'),
        ('reschedule_requested', 'Reschedule Requested'),
        ('rescheduled_done', 'Successfully Rescheduled'),
    ], string='WhatsApp Status', index=True, tracking=True)

    reschedule_requested_at = fields.Datetime(string='Reschedule Requested At', readonly=True)
    rescheduled_at = fields.Datetime(string='Rescheduled At', readonly=True)

    def action_mark_rescheduled(self):
        """Allows reception staff to resolve a reschedule request after phone follow-up."""
        for rec in self:
            rec.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                'wa_response_status': 'rescheduled_done',
                'notification_status': 'rescheduled_done',
                'rescheduled_at': fields.Datetime.now(),
            })
            rec.message_post(body=_(
                "<b>Reschedule Resolved:</b> Staff coordinated with patient and marked slot as successfully rescheduled."
            ))
        return True

    def action_resend_appointment_whatsapp(self):
        """Re-dispatches updated appointment details and marks the reschedule as completed."""
        self.ensure_one()
        template = self.env['whatsapp.template'].sudo().search([
            ('model', '=', self._name),
            ('active', '=', True)
        ], limit=1)
        if not template:
            template = self.env.ref('clinic_whatsapp.whatsapp_template_therapy_comm', raise_if_not_found=False)
        if not template:
            raise UserError(_("No active WhatsApp template found for Clinic Appointments."))

        # Re-dispatch with updated slot details
        created_queue = template.sudo().send_messages(self)
        if created_queue:
            self.with_context(bypass_matrix_lock=True, bypass_notification_reset=True).write({
                'notification_status': 'wa_delivered',
                'wa_response_status': 'rescheduled_done',
                'rescheduled_at': fields.Datetime.now(),
            })
            self.message_post(body=_(
                "<b>WhatsApp Updated Confirmation Sent:</b> New slot details dispatched to patient. Reschedule marked as resolved."
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