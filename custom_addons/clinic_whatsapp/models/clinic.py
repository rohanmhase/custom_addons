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
    # Dedicated Clinic Admin & Helpline Endpoints
    admin_name = fields.Char(
        string='Admin Name',
        help="Name of the clinic administrator (e.g. Pooja Sharma)"
    )
    admin_phone = fields.Char(
        string='Admin Calling Number',
        help="Direct mobile number for patients to call regarding queries or rescheduling"
    )
    admin_whatsapp = fields.Char(
        string='Admin WhatsApp Number',
        help="WhatsApp number used for direct patient chat redirection"
    )
    helpline_phone = fields.Char(
        string='Clinic Helpline / Landline',
        help="Front desk reception or landline phone number"
    )
    admin_name_2 = fields.Char(
        string='Secondary Admin Name',
        help="Second contact person (e.g. Rahul Patil)",
    )
    admin_phone_2 = fields.Char(
        string='Secondary Admin Phone',
        help="Direct mobile number for the second clinic contact",
    )

    def get_helpline_display(self):
        """Returns clean dynamic helpline string for templates & session texts."""
        self.ensure_one()
        contacts = []
        if self.admin_phone:
            contacts.append(f"{self.admin_name} ({self.admin_phone})" if self.admin_name else self.admin_phone)
        if self.admin_phone_2:
            contacts.append(f"{self.admin_name_2} ({self.admin_phone_2})" if self.admin_name_2 else self.admin_phone_2)

        if contacts:
            return " / ".join(contacts)
        if self.helpline_phone:
            return self.helpline_phone
        return getattr(self, 'phone', False) or '9082777624'