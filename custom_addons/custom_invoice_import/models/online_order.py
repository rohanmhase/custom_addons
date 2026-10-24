from odoo import models, fields

class OnlineOrder(models.Model):
    _name = 'online.order'
    _description = 'Online Order'

    name = fields.Char(string='Order Number', required=True, index=True)
    move_id = fields.Many2one('account.move', string='Invoice', ondelete='set null')
    payment_method = fields.Selection([
        ('prepaid', 'Prepaid'),
        ('pod', 'Pay on Delivery'),
    ], string='Payment Method')
    delivery_date = fields.Date(string='Delivery Date')
    utr = fields.Char(string='UTR')