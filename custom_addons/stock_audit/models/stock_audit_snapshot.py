# -*- coding: utf-8 -*-
import logging
import time
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class StockAuditSnapshot(models.Model):
    _name = 'stock.audit.snapshot'
    _description = 'Stock Audit Snapshot (Point-in-time stock balance)'
    _order = 'snapshot_date desc'
    _rec_name = 'snapshot_date'

    snapshot_date = fields.Date(
        string='Snapshot Date',
        required=True,
        index=True,
        help="The date for which stock balances are captured. "
             "Represents stock state at END of this day (23:59:59)."
    )
    state = fields.Selection(
        selection=[
            ('in_progress', 'In Progress'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        string='Status',
        default='in_progress',
        required=True,
        index=True,
    )
    line_ids = fields.One2many(
        comodel_name='stock.audit.snapshot.line',
        inverse_name='snapshot_id',
        string='Snapshot Lines',
    )
    line_count = fields.Integer(
        string='Records',
        compute='_compute_line_count',
        store=True,
    )
    created_by = fields.Selection(
        selection=[
            ('cron', 'Cron (Automatic)'),
            ('manual', 'Manual (Rebuild)'),
        ],
        string='Created By',
        default='cron',
        required=True,
    )
    duration_seconds = fields.Float(
        string='Build Duration (sec)',
        readonly=True,
        help="Time taken to build this snapshot."
    )
    note = fields.Text(
        string='Notes',
        help="Failure reason or manual comment."
    )

    _sql_constraints = [
        (
            'snapshot_date_unique',
            'UNIQUE(snapshot_date)',
            'A snapshot for this date already exists.'
        ),
    ]

    # ------------------------------------------------------------------
    # COMPUTES
    # ------------------------------------------------------------------
    @api.depends('line_ids')
    def _compute_line_count(self):
        """Efficient count using read_group; avoids loading all lines into memory."""
        data = self.env['stock.audit.snapshot.line'].read_group(
            domain=[('snapshot_id', 'in', self.ids)],
            fields=['snapshot_id'],
            groupby=['snapshot_id'],
        )
        counts = {d['snapshot_id'][0]: d['snapshot_id_count'] for d in data}
        for rec in self:
            rec.line_count = counts.get(rec.id, 0)

    # ------------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------------
    def action_rebuild(self):
        """Manual rebuild button. Restricted to Settings Admins.
        Deletes existing lines and re-runs the snapshot SQL for the same date.
        """
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise UserError(
                "Only Settings Administrators can rebuild snapshots."
            )

        # Verify config is set
        our_company_ids = self.env['stock.audit.config'].sudo().get_our_company_ids()
        if not our_company_ids:
            raise UserError(
                "No root companies configured.\n"
                "Please set them in Inventory > Configuration > Stock Audit Configuration."
            )

        # Delete existing lines and reset state
        self.line_ids.unlink()
        self.write({
            'state': 'in_progress',
            'note': False,
            'duration_seconds': 0.0,
        })

        Cron = self.env['stock.audit.snapshot.cron'].sudo()
        started = time.time()
        try:
            inserted = Cron._sql_populate_snapshot_lines(
                snapshot_id=self.id,
                snapshot_date=self.snapshot_date,
                our_company_ids=our_company_ids,
            )
            self.write({
                'state': 'done',
                'duration_seconds': round(time.time() - started, 2),
                'created_by': 'manual',
            })
            _logger.info(
                "[Stock Audit] Manual rebuild of snapshot %s done: %d lines in %.2fs",
                self.snapshot_date, inserted, self.duration_seconds,
            )
        except Exception as e:
            self.write({
                'state': 'failed',
                'note': "Rebuild failed: %s" % str(e)[:500],
            })
            _logger.exception(
                "[Stock Audit] Manual rebuild failed for snapshot %s", self.snapshot_date,
            )
            raise

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Snapshot Rebuilt',
                'message': 'Snapshot for %s rebuilt successfully (%d lines).' % (
                    self.snapshot_date, self.line_count,
                ),
                'type': 'success',
                'sticky': False,
            }
        }


class StockAuditSnapshotLine(models.Model):
    _name = 'stock.audit.snapshot.line'
    _description = 'Stock Audit Snapshot Line'
    _rec_name = 'product_id'

    snapshot_id = fields.Many2one(
        comodel_name='stock.audit.snapshot',
        string='Snapshot',
        required=True,
        ondelete='cascade',
        index=True,
    )
    snapshot_date = fields.Date(
        related='snapshot_id.snapshot_date',
        store=True,
        index=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Product',
        required=True,
        index=True,
        ondelete='restrict',
    )
    warehouse_id = fields.Many2one(
        comodel_name='stock.warehouse',
        string='Warehouse',
        required=True,
        index=True,
        ondelete='restrict',
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Company',
        required=True,
        index=True,
    )
    qty = fields.Float(
        string='Quantity',
        required=True,
        digits='Product Unit of Measure',
    )

    _sql_constraints = [
        (
            'snapshot_product_warehouse_unique',
            'UNIQUE(snapshot_id, product_id, warehouse_id)',
            'Duplicate snapshot line for same product+warehouse in one snapshot.'
        ),
    ]

    def init(self):
        """Composite index for the exact WHERE pattern used by audit report queries."""
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS 
                stock_audit_snapshot_line_lookup_idx 
            ON stock_audit_snapshot_line 
                (snapshot_date, warehouse_id, product_id)
        """)