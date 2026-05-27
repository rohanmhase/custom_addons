from odoo import models, fields

class ResCompany(models.Model):
    _inherit = 'res.company'

    invoice_prefix = fields.Char(
        string='Invoice Prefix',
        help='Short prefix for invoice numbering e.g. KN for Karnataka'
    )