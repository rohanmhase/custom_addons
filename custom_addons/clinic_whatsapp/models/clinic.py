import logging
from odoo import models, fields

_logger = logging.getLogger(__name__)

class ClinicClinic(models.Model):
    _inherit = 'clinic.clinic'

    sender_phone_id = fields.Many2one(
        'whatsapp.sender.phone',
        string='WhatsApp Sender Line',
        help="Specific Meta WhatsApp line used for this clinic branch. If empty, falls back to default."
    )
    wa_phone_number_id = fields.Char(
        related='sender_phone_id.phone_number_id',
        string='Meta Sender Phone ID',
        readonly=True
    )

    # Dynamic Admin Contact Helplines
    admin_name = fields.Char(string='Admin 1 Name')
    admin_phone = fields.Char(string='Admin 1 Phone')

    admin_name_2 = fields.Char(string='Admin 2 Name')
    admin_phone_2 = fields.Char(string='Admin 2 Phone')

    admin_name_3 = fields.Char(string='Admin 3 Name')
    admin_phone_3 = fields.Char(string='Admin 3 Phone')

    admin_whatsapp = fields.Char(
        string='Admin WhatsApp Number',
        help="WhatsApp number used for direct patient chat redirection"
    )
    helpline_phone = fields.Char(string='Clinic Helpline / Landline')

    def get_helpline_display(self):
        """Dynamically chains all active admin phone numbers with fallback."""
        self.ensure_one()
        contacts = []
        for name, phone in [
            (self.admin_name, self.admin_phone),
            (self.admin_name_2, self.admin_phone_2),
            (getattr(self, 'admin_name_3', False), getattr(self, 'admin_phone_3', False)),
        ]:
            if phone and str(phone).strip():
                clean_p = str(phone).strip()
                contacts.append(f"{name} ({clean_p})" if name else clean_p)

        if contacts:
            return " / ".join(contacts)
        if self.helpline_phone and str(self.helpline_phone).strip():
            return str(self.helpline_phone).strip()

        fallback_phone = getattr(self, 'phone', False)
        return str(fallback_phone).strip() if fallback_phone else '9082777624'