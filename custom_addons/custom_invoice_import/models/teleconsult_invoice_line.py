from odoo import models, fields


class TeleconsultInvoiceLine(models.Model):
    _name = 'teleconsult.invoice.line'
    _description = 'Tele-consultation Medicine Line'
    _order = 'id'

    move_id = fields.Many2one(
        'account.move', string='Invoice',
        required=True, ondelete='cascade', index=True,
    )
    product_name = fields.Char(string='Item', required=True)
    quantity = fields.Float(string='Qty', required=True, default=1.0)