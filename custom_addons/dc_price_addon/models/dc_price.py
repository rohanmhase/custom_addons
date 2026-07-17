from odoo import models, fields, api
from odoo.exceptions import ValidationError


class StockMove(models.Model):
    _inherit = 'stock.move'

    unit_price = fields.Float(
        string='Unit Price',
        digits='Product Price',
        help='Optional unit price for delivery challan',
        copy=True,
    )
    price_subtotal = fields.Float(
        string='Subtotal',
        compute='_compute_price_subtotal',
        store=True,
        digits='Product Price',
        copy=False,
    )

    @api.depends('unit_price', 'product_uom_qty', 'quantity')
    def _compute_price_subtotal(self):
        for move in self:
            qty = move.quantity if move.quantity else move.product_uom_qty
            move.price_subtotal = (move.unit_price or 0.0) * (qty or 0.0)

    @api.onchange('product_id')
    def _onchange_product_id_set_price(self):
        for move in self:
            if move.product_id and not move.unit_price:
                move.unit_price = move.product_id.lst_price

    @api.constrains('unit_price')
    def _check_unit_price_positive(self):
        for move in self:
            if move.unit_price < 0:
                raise ValidationError("Unit Price cannot be negative.")


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    def _get_aggregated_product_quantities(self, **kwargs):
        """Include unit_price in aggregated lines for delivery slip"""
        aggregated_lines = super()._get_aggregated_product_quantities(**kwargs)
        for line_key, line_data in aggregated_lines.items():
            product = line_data.get('product')
            if product:
                matching_ml = self.filtered(lambda ml: ml.product_id == product)
                line_data['unit_price'] = matching_ml[0].move_id.unit_price if matching_ml else 0.0
            else:
                line_data['unit_price'] = 0.0
        return aggregated_lines


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    amount_total = fields.Float(
        string='Grand Total',
        compute='_compute_amount_total',
        store=True,
        digits='Product Price',
        copy=False,
    )

    show_price_columns = fields.Boolean(
        string='Show Price Columns',
        compute='_compute_show_price_columns',
    )

    @api.depends('picking_type_id', 'picking_type_id.code')
    def _compute_show_price_columns(self):
        for picking in self:
            picking.show_price_columns = picking.picking_type_id.code == 'outgoing'

    @api.depends('move_ids_without_package.price_subtotal')
    def _compute_amount_total(self):
        for picking in self:
            picking.amount_total = sum(picking.move_ids_without_package.mapped('price_subtotal'))