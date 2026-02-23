from odoo import models, fields, api
from odoo.exceptions import UserError


class ClinicStockReplenishment(models.Model):
    _name = 'clinic.stock.replenishment'
    _description = 'Clinic Stock Replenishment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, default="New", tracking=True)
    date = fields.Date(default=fields.Date.today, tracking=True)

    active = fields.Boolean(default=True, tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated')
    ], default='draft', tracking=True)

    source_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Source Warehouse",
        required=True,
        tracking=True
    )

    destination_warehouse_ids = fields.Many2many(
        'stock.warehouse',
        string="Destination Warehouses",
        required=True,
        tracking=True
    )

    region_id = fields.Many2one(
        'clinic.stock.region',
        string="Region",
        tracking=True
    )

    # -------------------------------
    # ARCHIVE INSTEAD OF DELETE
    # -------------------------------

    def unlink(self):
        # If record is active, archive it instead (send to recycle bin)
        active_records = self.filtered('active')
        if active_records:
            active_records.write({'active': False})

        # If record is already archived, hard delete it
        inactive_records = self.filtered(lambda r: not r.active)
        if inactive_records:
            return super(StockCountFormula, inactive_records).unlink()

        return True

    # -------------------------------
    # CORE SHORTAGE CALCULATION
    # -------------------------------

    def _compute_shortage(self, product, warehouse):
        self.ensure_one()

        # Get formula rule for this clinic + product
        rule = self.env['stock.count.formula'].search([
            ('clinic_id', '=', warehouse.id),
            ('product_id', '=', product.id),
        ], limit=1)

        if rule:
            therapy_count = rule.get_yesterday_therapy_count()
            print("DEBUG → Clinic:", warehouse.name)
            print("DEBUG → Product:", product.display_name)
            print("DEBUG → Therapy Count:", therapy_count)

            target_qty = rule.calculate_price(therapy_count)
            print("DEBUG → Target Qty:", target_qty)
        else:
            target_qty = 0.0

        # Get available stock
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'child_of', warehouse.lot_stock_id.id),
        ])

        available_qty = sum(q.quantity - q.reserved_quantity for q in quants)
        print("DEBUG → Available Qty:", available_qty)

        shortage = target_qty - available_qty
        print("DEBUG → Shortage:", shortage)

        return shortage if shortage > 0 else 0.0

    # -------------------------------
    # GENERATE INTERNAL TRANSFERS
    # -------------------------------

    def action_generate_transfers(self):
        self.ensure_one()

        if not self.destination_warehouse_ids:
            raise UserError("Select at least one destination warehouse.")

        products = self.env['product.product'].search([
            ('type', '=', 'product')
        ])

        for warehouse in self.destination_warehouse_ids:

            move_lines = []

            for product in products:
                shortage = self._compute_shortage(product, warehouse)

                if shortage > 0:
                    move_lines.append((0, 0, {
                        'name': product.display_name,
                        'product_id': product.id,
                        'product_uom_qty': shortage,
                        'product_uom': product.uom_id.id,
                        'location_id': self.source_warehouse_id.lot_stock_id.id,
                        'location_dest_id': warehouse.lot_stock_id.id,
                    }))

            # If no shortage, skip this warehouse
            if not move_lines:
                continue

            # Get internal picking type for source warehouse
            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('warehouse_id', '=', self.source_warehouse_id.id),
            ], limit=1)

            if not picking_type:
                raise UserError("No internal picking type found for source warehouse.")

            # Create picking
            self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'location_id': self.source_warehouse_id.lot_stock_id.id,
                'location_dest_id': warehouse.lot_stock_id.id,
                'move_ids_without_package': move_lines,
                'origin': self.name,
            })

        # Update state AFTER processing all warehouses
        self.state = 'generated'

    # -------------------------------
    # REGION AUTO-FILL
    # -------------------------------

    @api.onchange('region_id')
    def _onchange_region_id(self):
        if self.region_id:
            self.destination_warehouse_ids = self.region_id.warehouse_ids