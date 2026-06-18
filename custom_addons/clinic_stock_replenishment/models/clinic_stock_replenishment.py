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

        if active_records:
            active_records.write({'active': False})
            return True

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

        dest_wh_ids = self.destination_warehouse_ids.ids
        today = date.today()

        # ----------------------------------------------------------------
        # STEP 1 — Prefetch all formula rules (1 ORM query)
        # Old: iterated per product/warehouse; New: single search, dict map
        # ----------------------------------------------------------------
        all_rules = self.env['stock.count.formula'].search([
            ('clinic_id', 'in', dest_wh_ids)
        ])
        products = all_rules.mapped('product_id')
        product_ids = products.ids

        rules_map = {
            (r.clinic_id.id, r.product_id.id): r
            for r in all_rules
        }

        # gender_filter map — avoids repeated attribute access in inner loop
        gender_filter_map = {
            (r.clinic_id.id, r.product_id.id): r.gender_filter
            for r in all_rules
        }

        # ----------------------------------------------------------------
        # STEP 2 — Bulk SQL aggregation for patient sessions (1 raw query)
        # Old: search_count × products × warehouses × 3 days + gender splits
        # New: one JOIN query grouped by warehouse/date/gender → Python dict
        # ----------------------------------------------------------------
        day_dates = [today - timedelta(days=i) for i in range(1, 4)]

        self.env.cr.execute("""
                    SELECT
                        sw.id                   AS warehouse_id,
                        ps.session_date         AS session_date,
                        pt.gender               AS gender,
                        COUNT(ps.id)            AS session_count
                    FROM patient_session ps
                    JOIN clinic_clinic cc
                        ON cc.id = ps.therapy_clinic_id
                    JOIN stock_warehouse sw
                        ON sw.id = cc.warehouse_id
                    JOIN clinic_patient pt
                        ON pt.id = ps.patient_id
                    WHERE
                        sw.id           = ANY(%s)
                        AND ps.session_date = ANY(%s)
                        AND ps.active       = TRUE
                    GROUP BY
                        sw.id, ps.session_date, pt.gender
                """, (dest_wh_ids, day_dates))
        # Build: session_map[(wh_id, date)] = {'male': X, 'female': Y, 'total': Z}
        session_map = defaultdict(lambda: {'male': 0, 'female': 0, 'total': 0})
        for row in self.env.cr.dictfetchall():
            key = (row['warehouse_id'], row['session_date'])
            gender = row['gender'] or 'unknown'
            cnt = row['session_count']
            session_map[key]['total'] += cnt
            if gender == 'male':
                session_map[key]['male'] += cnt
            elif gender == 'female':
                session_map[key]['female'] += cnt

        # Pre-compute per-warehouse therapy data fully in Python memory
        # therapy_summary[(wh_id)] = (daily_counts[3], max_index, male_on_max, female_on_max)
        therapy_summary = {}
        for wh_id in dest_wh_ids:
            daily_counts = [
                session_map[(wh_id, today - timedelta(days=i))]['total']
                for i in range(1, 4)
            ]
            max_index = daily_counts.index(max(daily_counts))
            max_day_date = today - timedelta(days=(max_index + 1))
            male_on_max   = session_map[(wh_id, max_day_date)]['male']
            female_on_max = session_map[(wh_id, max_day_date)]['female']
            therapy_summary[wh_id] = (daily_counts, max_index, male_on_max, female_on_max)

        # ----------------------------------------------------------------
        # STEP 3 — Batch fetch phantom (kit) BOMs (2 ORM queries total)
        # Old: mrp.bom search inside inner loop per product, per warehouse
        # New: fetch all phantom BOMs + lines upfront → dict keyed by product
        # ----------------------------------------------------------------
        # Map: product_id → bom_line list of (component_product_id, qty)
        phantom_bom_map = {}   # {product_id: [(comp_product_id, line_qty), ...]}

        # Match by specific product variant first
        boms_by_variant = self.env['mrp.bom'].search([
            ('product_id', 'in', product_ids),
            ('type', '=', 'phantom'),
        ])
        for bom in boms_by_variant:
            pid = bom.product_id.id
            if pid not in phantom_bom_map:
                phantom_bom_map[pid] = [
                    (line.product_id.id, line.product_qty)
                    for line in bom.bom_line_ids
                    if line.product_qty > 0
                ]

        # Fallback: match by product template for products not already found
        tmpl_ids_needed = [
            p.product_tmpl_id.id for p in products
            if p.id not in phantom_bom_map
        ]
        if tmpl_ids_needed:
            boms_by_tmpl = self.env['mrp.bom'].search([
                ('product_tmpl_id', 'in', tmpl_ids_needed),
                ('product_id', '=', False),   # template-level BOMs have no variant set
                ('type', '=', 'phantom'),
            ])
            # Build a tmpl→bom map; take first BOM per template
            tmpl_bom_map = {}
            for bom in boms_by_tmpl:
                tid = bom.product_tmpl_id.id
                if tid not in tmpl_bom_map:
                    tmpl_bom_map[tid] = [
                        (line.product_id.id, line.product_qty)
                        for line in bom.bom_line_ids
                        if line.product_qty > 0
                    ]
            # Assign to products that still have no BOM entry
            for p in products:
                if p.id not in phantom_bom_map:
                    lines = tmpl_bom_map.get(p.product_tmpl_id.id)
                    if lines:
                        phantom_bom_map[p.id] = lines

        # ----------------------------------------------------------------
        # STEP 4 — Batch fetch all stock.quant records (1 ORM query)
        # Old: search stock.quant inside get_kit_stock per product per wh
        # New: single search on all dest locations → quant_map in RAM
        # ----------------------------------------------------------------
        dest_location_ids = self.destination_warehouse_ids.mapped('lot_stock_id').ids

        # Collect all component product IDs needed for kit calculations
        kit_component_ids = set()
        for lines in phantom_bom_map.values():
            for comp_pid, _ in lines:
                kit_component_ids.add(comp_pid)

        all_product_ids_needed = list(set(product_ids) | kit_component_ids)


        all_quants = self.env['stock.quant'].search([
            ('product_id', 'in', all_product_ids_needed),
            ('location_id', 'child_of', dest_location_ids),
        ])

        # quant_map[(product_id, location_root_id)] = available_qty
        # We need per-warehouse totals; map location_id → warehouse
        loc_to_wh = {
            wh.lot_stock_id.id: wh.id
            for wh in self.destination_warehouse_ids
        }
        # For child locations we resolve via the quant's location hierarchy.
        # Build: quant_map[(product_id, warehouse_id)] = total available qty
        quant_map = defaultdict(float)
        for q in all_quants:
            # Walk up to find the root lot_stock_id warehouse match
            loc = q.location_id
            wh_id = None
            # Check direct or climb via parent chain (max 5 levels deep)
            check_loc = loc
            for _ in range(6):
                if check_loc.id in loc_to_wh:
                    wh_id = loc_to_wh[check_loc.id]
                    break
                if not check_loc.location_id:
                    break
                check_loc = check_loc.location_id
            if wh_id:
                quant_map[(q.product_id.id, wh_id)] += (q.quantity - q.reserved_quantity)
        # ----------------------------------------------------------------
        # STEP 5 — Build final stock map with kit fallback (pure Python)
        # Old: get_kit_stock() ran DB queries per product; New: RAM lookup only
        # ----------------------------------------------------------------
        def resolve_stock(product_id, wh_id):
            lines = phantom_bom_map.get(product_id)
            if lines:
                kit_qty = float('inf')
                for comp_pid, line_qty in lines:
                    comp_stock = quant_map.get((comp_pid, wh_id), 0.0)
                    kit_qty = min(kit_qty, comp_stock / line_qty)
                return kit_qty if kit_qty != float('inf') else 0.0

            return quant_map.get((product_id, wh_id), 0.0)

        # Pre-compute picking type once (1 query, reused)
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'internal'),
            ('warehouse_id', '=', self.source_warehouse_id.id),
        ], limit=1)
        if not picking_type:
            raise UserError("No internal picking type found for source warehouse.")

        # ----------------------------------------------------------------
        # STEP 6 — Main loop: zero DB queries inside, pure Python math
        # Old: N queries per product per warehouse; New: dict/RAM lookups only
        # ----------------------------------------------------------------
        all_log_vals = []

        for warehouse in self.destination_warehouse_ids:
            wh_id = warehouse.id
            daily_counts, max_index, male_on_max, female_on_max = therapy_summary[wh_id]
            max_count = max(daily_counts) if daily_counts else 0
            gender_label = f"{male_on_max}M / {female_on_max}F"

            move_lines = []

            for product in products:
                rule = rules_map.get((wh_id, product.id))
                gender_filter = gender_filter_map.get((wh_id, product.id), 'all')

                if rule:
                    # Resolve formula_count from prefetched therapy data
                    if gender_filter == 'male':
                        formula_count = male_on_max
                    elif gender_filter == 'female':
                        formula_count = female_on_max
                    else:
                        formula_count = max_count

                    target_qty = rule.calculate_price(formula_count)
                else:
                    formula_count = max_count
                    target_qty = 0.0

                current_stock = resolve_stock(product.id, wh_id)
                shortage = max(target_qty - current_stock, 0.0)

                all_log_vals.append({
                    'replenishment_id': self.id,
                    'snapshot_datetime': fields.Datetime.now(),
                    'source_warehouse_id': self.source_warehouse_id.id,
                    'destination_warehouse_id': wh_id,
                    'product_id': product.id,
                    'day_1_count': daily_counts[0] if len(daily_counts) > 0 else 0,
                    'day_2_count': daily_counts[1] if len(daily_counts) > 1 else 0,
                    'day_3_count': daily_counts[2] if len(daily_counts) > 2 else 0,
                    'max_therapy_count': max_count,
                    'gender_session_count': gender_label,
                    'target_qty': target_qty,
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

            if move_lines:
                self.env['stock.picking'].create({
                    'picking_type_id': picking_type.id,
                    'location_id': self.source_warehouse_id.lot_stock_id.id,
                    'location_dest_id': warehouse.lot_stock_id.id,
                    'move_ids_without_package': move_lines,
                    'origin': self.name,
                })

        # Single bulk log create across ALL warehouses (1 write vs N×M writes)
        if all_log_vals:
            self.env['clinic.stock.replenishment.log'].create(all_log_vals)

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