from odoo import models, fields, api, _

class WhatsappSenderPhone(models.Model):
    _name = 'whatsapp.sender.phone'
    _description = 'WhatsApp Meta Sender Phone Number'
    _order = 'is_default desc, name asc'

    name = fields.Char(
        string='Sender Profile Name',
        required=True,
        help="e.g. 'Invoicing & Bills', 'Clinic Scheduling', 'Vashi Clinic Line'"
    )
    phone_number = fields.Char(
        string='Display Phone Number',
        help="e.g. +91 87794 06969"
    )
    phone_number_id = fields.Char(
        string='Meta Phone Number ID',
        required=True,
        help="15-16 digit Meta Phone Number ID (e.g. 1395062320356609)"
    )
    waba_id = fields.Char(
        string='WABA ID (Optional)',
        help="Specify if this number belongs to a separate WhatsApp Business Account"
    )
    is_default = fields.Boolean(
        string='Default System Sender',
        default=False,
        help="Used as fallback when neither template nor clinic specifies a sender"
    )
    notes = fields.Text(string='Internal Notes')
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('is_default'):
                self.search([('is_default', '=', True)]).write({'is_default': False})
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('is_default'):
            self.search([('is_default', '=', True), ('id', 'not in', self.ids)]).write({'is_default': False})
        return super().write(vals)

    def name_get(self):
        res = []
        for rec in self:
            label = f"{rec.name} ({rec.phone_number or rec.phone_number_id})"
            if rec.is_default:
                label += " [Default]"
            res.append((rec.id, label))
        return res