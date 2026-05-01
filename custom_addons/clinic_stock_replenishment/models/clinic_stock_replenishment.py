from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime, timedelta, date
from collections import defaultdict


class ClinicStockReplenishment(models.Model):
    _name = 'clinic.stock.replenishment'
    _description = 'Clinic Stock Replenishment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        readonly=True,
        default="New")
    date = fields.Date(default=lambda self: self._ist_date(), tracking=True, readonly=True)

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
    log_ids = fields.One2many(
        'clinic.stock.replenishment.log',
        'replenishment_id',
        string="Snapshot Log"
    )

    # -------------------------------
    # ARCHIVE INSTEAD OF DELETE
    # -------------------------------

    def unlink(self):
        active_records = self.filtered(lambda r: r.active)
        archived_records = self - active_records

        # Archive active ones
        if active_records:
            active_records.write({'active': False})
            return True

        # Permanently delete already archived ones
        if archived_records:
            super(ClinicStockReplenishment, archived_records).unlink()

        return True

    # -------------------------------
    # GENERATE INTERNAL TRANSFERS
    # -------------------------------

    def action_generate_transfers(self):
        self.ensure_one()

        if not self.destination_warehouse_ids:
            raise UserError("Select at least one destination warehouse.")

        # --- PREFETCH: one query for all rules ---
        all_rules = self.env['stock.count.formula'].search([
            ('clinic_id', 'in', self.destination_warehouse_ids.ids)
        ])

        products = all_rules.mapped('product_id')
        rules_map = {
            (r.clinic_id.id, r.product_id.id): r
            for r in all_rules
        }

        # --- PREFETCH: one query for all quants across all destination warehouses ---
        all_stock_location_ids = self.destination_warehouse_ids.mapped('lot_stock_id').ids
        all_quants = self.env['stock.quant'].search([
            ('product_id', 'in', products.ids),
            ('location_id', 'child_of', all_stock_location_ids),
        ])

        quants_map = defaultdict(float)
        for q in all_quants:
            for wh in self.destination_warehouse_ids:
                if str(wh.lot_stock_id.id) in q.location_id.parent_path.split('/'):
                    quants_map[(q.product_id.id, wh.id)] += q.quantity - q.reserved_quantity
                    break

        for warehouse in self.destination_warehouse_ids:

            move_lines = []
            log_vals = []

            for product in products:
                # --- dict lookup, zero DB queries ---
                rule = rules_map.get((warehouse.id, product.id))

                therapy_data = rule.get_yesterday_therapy_count() if rule else [0, 0, 0]
                max_count = max(therapy_data) if therapy_data else 0

                current_stock = quants_map[(product.id, warehouse.id)]

                if rule:
                    therapy_count = max(therapy_data)
                    target_qty = rule.calculate_price(therapy_count)
                else:
                    target_qty = 0.0

                shortage = max(target_qty - current_stock, 0.0)

                log_vals.append({
                    'replenishment_id': self.id,
                    'source_warehouse_id': self.source_warehouse_id.id,
                    'destination_warehouse_id': warehouse.id,
                    'product_id': product.id,
                    'day_1_count': therapy_data[0] if len(therapy_data) > 0 else 0,
                    'day_2_count': therapy_data[1] if len(therapy_data) > 1 else 0,
                    'day_3_count': therapy_data[2] if len(therapy_data) > 2 else 0,
                    'max_therapy_count': max_count,
                    'target_qty': (current_stock + shortage) if shortage > 0 else current_stock,
                    'current_stock': current_stock,
                    'shortage_qty': shortage,
                })

                if shortage > 0:
                    move_lines.append((0, 0, {
                        'name': product.display_name,
                        'product_id': product.id,
                        'product_uom_qty': shortage,
                        'product_uom': product.uom_id.id,
                        'location_id': self.source_warehouse_id.lot_stock_id.id,
                        'location_dest_id': warehouse.lot_stock_id.id,
                    }))

            # --- single batch create instead of one per product ---
            self.env['clinic.stock.replenishment.log'].create(log_vals)

            if not move_lines:
                continue

            picking_type = self.env['stock.picking.type'].search([
                ('code', '=', 'internal'),
                ('warehouse_id', '=', self.source_warehouse_id.id),
            ], limit=1)

            if not picking_type:
                raise UserError("No internal picking type found for source warehouse.")

            self.env['stock.picking'].create({
                'picking_type_id': picking_type.id,
                'location_id': self.source_warehouse_id.lot_stock_id.id,
                'location_dest_id': warehouse.lot_stock_id.id,
                'move_ids_without_package': move_lines,
                'origin': self.name,
            })

        self.state = 'generated'

    def action_open_log(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Snapshot Log',
            'res_model': 'clinic.stock.replenishment.log',
            'view_mode': 'tree',
            'domain': [('replenishment_id', '=', self.id)],
            'context': {'search_default_has_shortage': 0},
        }

    # -------------------------------
    # REGION AUTO-FILL
    # -------------------------------

    @api.onchange('region_id')
    def _onchange_region_id(self):
        if self.region_id:
            self.destination_warehouse_ids = self.region_id.warehouse_ids

    @api.model
    def create(self, vals):
        if vals.get('name', 'New') == 'New':
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'clinic.stock.replenishment'
            ) or 'New'
        return super().create(vals)

    def _ist_date(self):
        utc = datetime.now()
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()