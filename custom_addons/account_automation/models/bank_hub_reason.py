from odoo import models, fields


class BankSalesAuditReason(models.Model):
    _name = 'bank.sales.audit.reason'
    _description = 'Bank vs HUB Variance Reason'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    resolution_type = fields.Selection([
        ('carry_forward', 'Carry Forward (Pending Settlement)'),
        ('settle_pending', 'Settle Against Past Pending'),
        ('write_off', 'Drop / Received via Other Channel (No Carry Forward)'),
    ], required=True, string='Behavior')

    help_text = fields.Char(string='Description')