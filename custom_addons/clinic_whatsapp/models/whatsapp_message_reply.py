import logging
from odoo import fields, models

_logger = logging.getLogger(__name__)


class WhatsappMessageReply(models.Model):
  _name = 'whatsapp.message.reply'
  _description = 'Inbound WhatsApp Patient Reply'
  _order = 'create_date desc'

  patient_name = fields.Char(string='Patient Name')
  phone = fields.Char(string='From Phone', required=True, index=True)
  wamid = fields.Char(string='Reply Message ID', readonly=True, index=True)
  context_wamid = fields.Char(
      string='Replied-To Message ID',
      readonly=True,
      index=True,
      help='ID of the outbound message being replied to',
  )

  reply_type = fields.Selection(
      [
          ('quick_reply', 'Quick Reply Button'),
          ('interactive', 'Interactive Button / List'),
          ('text', 'Text Message'),
      ],
      string='Response Type',
      default='quick_reply',
  )

  selected_option = fields.Char(
      string='Selected Button / Choice',
      help="Button text selected by the patient (e.g. 'Confirm Appointment')",
  )
  message_body = fields.Text(string='Full Response Content')

  queue_id = fields.Many2one(
      'whatsapp.message.queue',
      string='Linked Outbound Message',
      ondelete='set null',
  )
  clinic_id = fields.Many2one(
      'clinic.clinic', string='Clinic Branch', ondelete='set null'
  )

  res_model = fields.Char(string='Source Model', index=True)
  res_id = fields.Integer(string='Source Record ID', index=True)