from odoo import models, fields

class AccountMoveNature(models.Model):
    """
    Dedicated custom table storing invoice nature mapping.
    Standard Odoo 'account_move' table schema remains 100% untouched.
    """
    _name = 'account.move.nature'
    _description = 'Invoice Nature Mapping'

    move_id = fields.Many2one(
        'account.move', string='Invoice', required=True, ondelete='cascade', index=True
    )
    nature_id = fields.Many2one(
        'res.partner.nature', string='Nature', required=True, ondelete='cascade'
    )
    partner_id = fields.Many2one(
        'res.partner', related='move_id.partner_id', string='Customer', store=True, index=True
    )

    _sql_constraints = [
        ('unique_move', 'unique(move_id)', 'An invoice can only have one nature link!'),
    ]