# -*- coding: utf-8 -*-
import logging
from datetime import timedelta
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockAuditWizard(models.TransientModel):
    _name = 'stock.audit.wizard'
    _description = 'Stock Audit Report Wizard'

    date_from = fields.Date(string='Date From', required=True)
    date_to = fields.Date(string='Date To', required=True, default=fields.Date.context_today)

    filter_type = fields.Selection(
        selection=[
            ('main', 'Main Warehouses Only'),
            ('region', 'By Region'),
            ('warehouse', 'By Warehouse(s)'),
            ('all', 'All Warehouses (Heavy)'),
        ],
        string='Filter',
        default='main',
        required=True,
    )
    region_id = fields.Many2one('stock.audit.region', string='Region')
    warehouse_ids = fields.Many2many('stock.warehouse', string='Warehouses')

    result_line_ids = fields.One2many(
        'stock.audit.report.line', 'wizard_id', string='Results'
    )

    # ------------------------------------------------------------------
    # ACTION
    # ------------------------------------------------------------------
    def action_generate(self):
        self.ensure_one()

        if self.date_from > self.date_to:
            raise UserError("Date From must be earlier than or equal to Date To.")

        if not self.env.user.has_group('stock.group_stock_manager'):
            raise UserError("Only Inventory Administrators can run the audit.")

        our_company_ids = self.env['stock.audit.config'].sudo().get_our_company_ids()
        if not our_company_ids:
            raise UserError(
                "No root companies configured.\n"
                "Please set them in Inventory > Configuration > Stock Audit Configuration."
            )

        warehouse_ids = self._resolve_warehouse_ids(our_company_ids)
        if not warehouse_ids:
            raise UserError("No warehouses match the selected filter.")

        # Clear previous results for this wizard
        self.result_line_ids.unlink()

        rows = self._run_audit_sql(our_company_ids, warehouse_ids)
        if not rows:
            raise UserError("No stock activity found for the selected range/warehouses.")

        # Bulk-create result lines
        self.env['stock.audit.report.line'].sudo().create([{
            'wizard_id': self.id,
            'product_id': r['product_id'],
            'warehouse_id': r['warehouse_id'],
            'opening_qty': r['opening_qty'],
            'issue_qty': r['issue_qty'],
            'receipt_qty': r['receipt_qty'],
            'sale_qty': r['sale_qty'],
            'closing_qty': r['closing_qty'],
        } for r in rows])

        return {
            'type': 'ir.actions.act_window',
            'name': 'Stock Audit Report',
            'res_model': 'stock.audit.report.line',
            'view_mode': 'tree',
            'domain': [('wizard_id', '=', self.id)],
            'context': {'search_default_group_warehouse': 1},
            'target': 'current',
        }

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------
    def _resolve_warehouse_ids(self, our_company_ids):
        """Return the list of warehouse IDs based on filter_type."""
        Warehouse = self.env['stock.warehouse'].sudo()

        if self.filter_type == 'warehouse':
            return self.warehouse_ids.ids

        if self.filter_type == 'region':
            if not self.region_id:
                raise UserError("Please select a region.")
            return self.region_id.warehouse_ids.ids

        if self.filter_type == 'all':
            return Warehouse.search([('company_id', 'in', our_company_ids)]).ids

        # 'main' — warehouses NOT linked to any POS config
        self.env.cr.execute("""
            SELECT w.id
            FROM stock_warehouse w
            WHERE w.company_id = ANY(%s)
              AND NOT EXISTS (
                  SELECT 1 FROM pos_config p WHERE p.warehouse_id = w.id
              )
        """, (list(our_company_ids),))
        return [r[0] for r in self.env.cr.fetchall()]

    # ------------------------------------------------------------------
    # CORE SQL — single query, all buckets
    # ------------------------------------------------------------------
    def _run_audit_sql(self, our_company_ids, warehouse_ids):
        """Run the audit SQL and return list of dicts.

        Approach:
          1. Opening = latest snapshot <= date_from + delta of moves from snapshot to date_from.
          2. Bucketed moves within [date_from, date_to] computed via UNION ALL:
               - source side => Issue or Sale (based on destination type + partner)
               - dest side   => Receipt
          3. Closing = Opening + Receipt - Issue - Sale.
        """
        date_from = self.date_from
        date_to = self.date_to

        # end-of-day cutoffs
        cutoff_from = fields.Datetime.to_datetime(date_from) + timedelta(days=1) - timedelta(seconds=1)
        cutoff_to   = fields.Datetime.to_datetime(date_to)   + timedelta(days=1) - timedelta(seconds=1)
        range_start = fields.Datetime.to_datetime(date_from)

        # Partner IDs of "our companies" (used to detect branch transfers)
        our_partner_ids = self.env['res.company'].sudo().browse(our_company_ids).mapped('partner_id').ids or [0]

        params = {
            'company_ids':   list(our_company_ids),
            'warehouse_ids': list(warehouse_ids),
            'our_partners':  list(our_partner_ids),
            'cutoff_from':   cutoff_from,
            'range_start':   range_start,
            'cutoff_to':     cutoff_to,
            'date_from':     date_from,
        }

        query = """
        WITH internal_locs AS (
            SELECT l.id AS location_id, w.id AS warehouse_id
            FROM stock_location l
            JOIN stock_location wvl ON TRUE
            JOIN stock_warehouse w
              ON w.view_location_id = wvl.id
             AND l.parent_path LIKE (wvl.parent_path || '%%')
            WHERE l.usage = 'internal'
              AND l.company_id = ANY(%(company_ids)s)
              AND w.id = ANY(%(warehouse_ids)s)
        ),

        -- ---------- OPENING ----------
        latest_snapshot AS (
            SELECT MAX(sl.snapshot_date) AS snap_date
            FROM stock_audit_snapshot_line sl
            JOIN stock_audit_snapshot s ON s.id = sl.snapshot_id
            WHERE s.state = 'done'
            AND sl.snapshot_date < %(date_from)s
            AND sl.warehouse_id = ANY(%(warehouse_ids)s)
        ),
        opening_from_snap AS (
            SELECT sl.product_id, sl.warehouse_id, sl.qty
            FROM stock_audit_snapshot_line sl
            JOIN stock_audit_snapshot s ON s.id = sl.snapshot_id
            WHERE s.state = 'done'
              AND sl.snapshot_date = (SELECT snap_date FROM latest_snapshot)
              AND sl.warehouse_id = ANY(%(warehouse_ids)s)
        ),
        delta_in AS (
            SELECT ml.product_id, il.warehouse_id, SUM(ml.quantity) AS qty
            FROM stock_move_line ml
            JOIN internal_locs il ON il.location_id = ml.location_dest_id
            WHERE ml.state = 'done'
              AND ml.date > COALESCE(
                    (SELECT snap_date + INTERVAL '1 day' - INTERVAL '1 second' FROM latest_snapshot),
                    '1900-01-01'::timestamp)
              AND ml.date < %(range_start)s
            GROUP BY ml.product_id, il.warehouse_id
        ),
        delta_out AS (
            SELECT ml.product_id, il.warehouse_id, SUM(ml.quantity) AS qty
            FROM stock_move_line ml
            JOIN internal_locs il ON il.location_id = ml.location_id
            WHERE ml.state = 'done'
              AND ml.date > COALESCE(
                    (SELECT snap_date + INTERVAL '1 day' - INTERVAL '1 second' FROM latest_snapshot),
                    '1900-01-01'::timestamp)
              AND ml.date < %(range_start)s
            GROUP BY ml.product_id, il.warehouse_id
        ),
        opening AS (
            SELECT
                COALESCE(s.product_id, i.product_id, o.product_id)       AS product_id,
                COALESCE(s.warehouse_id, i.warehouse_id, o.warehouse_id) AS warehouse_id,
                (COALESCE(s.qty, 0) + COALESCE(i.qty, 0) - COALESCE(o.qty, 0)) AS qty
            FROM opening_from_snap s
            FULL OUTER JOIN delta_in i
              ON s.product_id = i.product_id AND s.warehouse_id = i.warehouse_id
            FULL OUTER JOIN delta_out o
              ON COALESCE(s.product_id, i.product_id) = o.product_id
             AND COALESCE(s.warehouse_id, i.warehouse_id) = o.warehouse_id
        ),

        -- ---------- IN-RANGE MOVES (source-side rows for Issue/Sale) ----------
        src_side AS (
            SELECT
                ml.product_id,
                il.warehouse_id,
                ml.quantity AS qty,
                dest.usage  AS dest_usage,
                m.partner_id
            FROM stock_move_line ml
            JOIN stock_move m       ON m.id = ml.move_id
            JOIN internal_locs il   ON il.location_id = ml.location_id
            JOIN stock_location dest ON dest.id = ml.location_dest_id
            WHERE ml.state = 'done'
              AND ml.date >= %(range_start)s
              AND ml.date <= %(cutoff_to)s
        ),
        issue_rows AS (
            SELECT product_id, warehouse_id, SUM(qty) AS qty
            FROM src_side
            WHERE
                dest_usage = 'internal'                                  -- inter-warehouse
                OR dest_usage IN ('inventory', 'production')             -- adj-out / scrap-like virtuals we count as issue
                OR dest_usage = 'supplier'                               -- return to vendor
                OR (dest_usage = 'customer' AND partner_id = ANY(%(our_partners)s))  -- branch transfer via challan
            GROUP BY product_id, warehouse_id
        ),
        sale_rows AS (
            SELECT product_id, warehouse_id, SUM(qty) AS qty
            FROM src_side
            WHERE dest_usage = 'customer'
              AND (partner_id IS NULL OR partner_id != ALL(%(our_partners)s))
            GROUP BY product_id, warehouse_id
        ),

        -- ---------- IN-RANGE MOVES (dest-side rows for Receipt) ----------
        receipt_rows AS (
            SELECT ml.product_id, il.warehouse_id, SUM(ml.quantity) AS qty
            FROM stock_move_line ml
            JOIN internal_locs il ON il.location_id = ml.location_dest_id
            WHERE ml.state = 'done'
              AND ml.date >= %(range_start)s
              AND ml.date <= %(cutoff_to)s
            GROUP BY ml.product_id, il.warehouse_id
        ),

        -- ---------- FINAL MERGE ----------
        all_keys AS (
            SELECT product_id, warehouse_id FROM opening
            UNION SELECT product_id, warehouse_id FROM issue_rows
            UNION SELECT product_id, warehouse_id FROM sale_rows
            UNION SELECT product_id, warehouse_id FROM receipt_rows
        )
        SELECT
            k.product_id,
            k.warehouse_id,
            COALESCE(op.qty, 0)  AS opening_qty,
            COALESCE(iss.qty, 0) AS issue_qty,
            COALESCE(rec.qty, 0) AS receipt_qty,
            COALESCE(sal.qty, 0) AS sale_qty,
            (COALESCE(op.qty, 0) + COALESCE(rec.qty, 0)
                - COALESCE(iss.qty, 0) - COALESCE(sal.qty, 0)) AS closing_qty
        FROM all_keys k
        LEFT JOIN opening      op  ON op.product_id  = k.product_id AND op.warehouse_id  = k.warehouse_id
        LEFT JOIN issue_rows   iss ON iss.product_id = k.product_id AND iss.warehouse_id = k.warehouse_id
        LEFT JOIN receipt_rows rec ON rec.product_id = k.product_id AND rec.warehouse_id = k.warehouse_id
        LEFT JOIN sale_rows    sal ON sal.product_id = k.product_id AND sal.warehouse_id = k.warehouse_id
        WHERE NOT (
            COALESCE(op.qty, 0)  = 0
            AND COALESCE(iss.qty, 0) = 0
            AND COALESCE(rec.qty, 0) = 0
            AND COALESCE(sal.qty, 0) = 0
        )
        ORDER BY k.warehouse_id, k.product_id
        """

        self.env.cr.execute(query, params)
        return self.env.cr.dictfetchall()


class StockAuditReportLine(models.TransientModel):
    _name = 'stock.audit.report.line'
    _description = 'Stock Audit Report Line'
    _order = 'warehouse_id, product_id'

    wizard_id = fields.Many2one('stock.audit.wizard', ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Warehouse', readonly=True)

    opening_qty = fields.Float(string='Opening Qty', readonly=True, digits='Product Unit of Measure')
    issue_qty   = fields.Float(string='Issue Qty',   readonly=True, digits='Product Unit of Measure')
    receipt_qty = fields.Float(string='Receipt Qty', readonly=True, digits='Product Unit of Measure')
    sale_qty    = fields.Float(string='Sale Qty',    readonly=True, digits='Product Unit of Measure')
    closing_qty = fields.Float(string='Closing Qty', readonly=True, digits='Product Unit of Measure')

    # Placeholders for future Rate/Amount columns
    opening_rate = fields.Float(string='Opening Rate', readonly=True)
    opening_amt  = fields.Float(string='Opening Amount', readonly=True)
    issue_rate   = fields.Float(string='Issue Rate', readonly=True)
    issue_amt    = fields.Float(string='Issue Amount', readonly=True)
    receipt_rate = fields.Float(string='Receipt Rate', readonly=True)
    receipt_amt  = fields.Float(string='Receipt Amount', readonly=True)
    sale_rate    = fields.Float(string='Sale Rate', readonly=True)
    sale_amt     = fields.Float(string='Sale Amount', readonly=True)
    closing_rate = fields.Float(string='Closing Rate', readonly=True)
    closing_amt  = fields.Float(string='Closing Amount', readonly=True)