from odoo import models, fields


class ResPartnerNature(models.Model):
    _name = 'res.partner.nature'
    _description = 'Customer Nature'
    _order = 'name'

    name = fields.Char(string='Nature Name', required=True, translate=True)
    code = fields.Char(string='Code', help='Technical short code')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('unique_code', 'unique(code)', 'Nature code must be unique!'),
    ]