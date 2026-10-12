import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)

class ClinicClinic(models.Model):
    _inherit = 'clinic.clinic'

    sender_phone_id = fields.Many2one(
        'whatsapp.sender.phone',
        string='WhatsApp Sender Line',
        help="Specific Meta WhatsApp line used for this clinic branch. If empty, falls back to the template or system default."
    )
    wa_phone_number_id = fields.Char(
        related='sender_phone_id.phone_number_id',
        string='Meta Sender Phone ID',
        readonly=True
    )