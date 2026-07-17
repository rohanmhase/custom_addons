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

    # ✅ FIX #1: Depend on BOTH product_uom_qty (Demand) AND quantity (done qty)
    @api.depends('unit_price', 'product_uom_qty', 'quantity')
    def _compute_price_subtotal(self):
        for move in self:
            # Use 'quantity' (done qty) if set, else fallback to 'product_uom_qty' (demand)
            qty = move.quantity if move.quantity else move.product_uom_qty
            move.price_subtotal = (move.unit_price or 0.0) * (qty or 0.0)

    @api.onchange('product_id')
    def _onchange_product_id_set_price(self):
        for move in self:
            if move.product_id and not move.unit_price:
                move.unit_price = move.product_id.lst_price

    # ✅ FIX #6: Validate negative unit price
    @api.constrains('unit_price')
    def _check_unit_price_positive(self):
        for move in self:
            if move.unit_price < 0:
                raise ValidationError("Unit Price cannot be negative.")


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    amount_total = fields.Float(
        string='Grand Total',
        compute='_compute_amount_total',
        store=True,
        digits='Product Price',
        copy=False,
    )

    # ✅ FIX #2: Helper to check if this picking type should show prices
    show_price_columns = fields.Boolean(
        string='Show Price Columns',
        compute='_compute_show_price_columns',
    )

    @api.depends('picking_type_id', 'picking_type_id.code')
    def _compute_show_price_columns(self):
        for picking in self:
            # Only show price columns for outgoing (delivery) pickings
            picking.show_price_columns = picking.picking_type_id.code == 'outgoing'

    @api.depends('move_ids_without_package.price_subtotal')
    def _compute_amount_total(self):
        for picking in self:
            picking.amount_total = sum(picking.move_ids_without_package.mapped('price_subtotal'))