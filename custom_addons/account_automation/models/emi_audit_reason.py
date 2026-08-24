from odoo import models, fields


class EmiAuditReason(models.Model):
    _name = 'emi.audit.reason'
    _description = 'EMI Audit Variance Reason'
    _order = 'sequence, id'
    _inherit = ['emi.soft.delete.mixin']

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)

    resolution_type = fields.Selection([
        ('carry_forward', 'Carry Forward (Pending Settlement)'),
        ('settle_pending', 'Settle Against Past Pending'),
        ('write_off', 'Drop / Write Off (No Carry Forward)'),
    ], required=True, string='Behavior')

    help_text = fields.Char(string='Description')