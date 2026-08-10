# -*- coding: utf-8 -*-
import logging
import time
from datetime import timedelta
from odoo import models, api, fields

_logger = logging.getLogger(__name__)

# Gap in days between consecutive snapshots.
# Start with 30 as agreed; can be tuned later.
SNAPSHOT_GAP_DAYS = 30

# Safety cap: max snapshots built in a single cron run.
# 9 months backfill = ~9 snapshots; 40 is a very safe upper bound.
MAX_ITERATIONS_PER_RUN = 40


class StockAuditSnapshotCron(models.AbstractModel):
    """Abstract model hosting cron methods for Stock Audit snapshots.
    No database table; pure logic container.
    """
    _name = 'stock.audit.snapshot.cron'
    _description = 'Stock Audit Snapshot Cron Runner'

    # ==================================================================
    # CRON ENTRY POINT
    # ==================================================================
    @api.model
    def _cron_build_snapshots(self):
        """Self-healing cron. Runs daily at 00:00 IST.

        Logic:
          - Verify root companies are configured.
          - Loop:
              * Find latest 'done' snapshot.
              * If none, start from earliest stock_move date.
              * Else, compute next_date = latest + SNAPSHOT_GAP_DAYS.
              * If gap <= SNAPSHOT_GAP_DAYS -> up-to-date, exit.
              * Else build snapshot for next_date.
          - Bounded by MAX_ITERATIONS_PER_RUN.
          - Commits after each snapshot for crash-safety.
        """
        # Guard 1: root companies must be configured
        our_company_ids = self.env['stock.audit.config'].sudo().get_our_company_ids()
        if not our_company_ids:
            _logger.warning(
                "[Stock Audit] No root companies configured. "
                "Set them in Inventory > Configuration > Stock Audit Configuration. "
                "Cron skipped."
            )
            return

        Snapshot = self.env['stock.audit.snapshot'].sudo()
        today = fields.Date.context_today(self)
        built = 0

        for _iteration in range(MAX_ITERATIONS_PER_RUN):
            latest = Snapshot.search(
                [('state', '=', 'done')],
                order='snapshot_date desc',
                limit=1,
            )

            if not latest:
                # First-ever snapshot: anchor at earliest stock movement date.
                next_date = self._get_first_snapshot_date(our_company_ids)
                if not next_date:
                    _logger.info(
                        "[Stock Audit] No done stock moves found for our companies. "
                        "Nothing to snapshot."
                    )
                    return
            else:
                gap = (today - latest.snapshot_date).days
                if gap <= SNAPSHOT_GAP_DAYS:
                    # Up-to-date
                    if built:
                        _logger.info(
                            "[Stock Audit] Cron finished. Built %d snapshot(s) this run.",
                            built,
                        )
                    return
                next_date = latest.snapshot_date + timedelta(days=SNAPSHOT_GAP_DAYS)

            # Never snapshot a future date
            if next_date > today:
                next_date = today

            # Build the snapshot; commit-per-snapshot for safety
            try:
                self._create_and_build_snapshot(
                    snapshot_date=next_date,
                    created_by='cron',
                    our_company_ids=our_company_ids,
                )
                built += 1
                # Commit so subsequent failures don't lose completed work
                self.env.cr.commit()
            except Exception as e:
                _logger.exception(
                    "[Stock Audit] Snapshot build FAILED for %s: %s",
                    next_date, e,
                )
                # Rollback current failed transaction; don't re-raise.
                # Next scheduled cron run will retry automatically.
                self.env.cr.rollback()
                return

        _logger.warning(
            "[Stock Audit] Reached MAX_ITERATIONS_PER_RUN (%d) in one run. "
            "Cron will continue at next scheduled invocation.",
            MAX_ITERATIONS_PER_RUN,
        )

    # ==================================================================
    # HELPERS
    # ==================================================================
    @api.model
    def _get_first_snapshot_date(self, our_company_ids):
        """Return earliest 'done' stock move date for our companies.
        Used as anchor for the very first snapshot.
        """
        self.env.cr.execute("""
            SELECT MIN(date)::date
            FROM stock_move
            WHERE state = 'done'
              AND company_id = ANY(%s)
        """, (list(our_company_ids),))
        row = self.env.cr.fetchone()
        return row[0] if row and row[0] else False

    @api.model
    def _create_and_build_snapshot(self, snapshot_date, created_by='cron', our_company_ids=None):
        """Create a snapshot header, populate lines via raw SQL, mark done.

        :param snapshot_date: date for stock balance capture (end-of-day 23:59:59).
        :param created_by: 'cron' or 'manual'.
        :param our_company_ids: pre-resolved list of company IDs (optional).
                                If not passed, will be fetched from config.
        :returns: the created snapshot record.
        """
        Snapshot = self.env['stock.audit.snapshot'].sudo()

        # Guard: avoid duplicate for same date
        existing = Snapshot.search([('snapshot_date', '=', snapshot_date)], limit=1)
        if existing:
            _logger.info(
                "[Stock Audit] Snapshot for %s already exists (id=%s). Skipping.",
                snapshot_date, existing.id,
            )
            return existing

        if our_company_ids is None:
            our_company_ids = self.env['stock.audit.config'].sudo().get_our_company_ids()
        if not our_company_ids:
            _logger.warning(
                "[Stock Audit] Cannot build snapshot for %s: no root companies configured.",
                snapshot_date,
            )
            return False

        started = time.time()
        snap = Snapshot.create({
            'snapshot_date': snapshot_date,
            'state': 'in_progress',
            'created_by': created_by,
        })

        try:
            inserted = self._sql_populate_snapshot_lines(
                snapshot_id=snap.id,
                snapshot_date=snapshot_date,
                our_company_ids=our_company_ids,
            )
            duration = time.time() - started
            snap.write({
                'state': 'done',
                'duration_seconds': round(duration, 2),
            })
            _logger.info(
                "[Stock Audit] Snapshot %s built: %d lines in %.2fs",
                snapshot_date, inserted, duration,
            )
        except Exception as e:
            snap.write({
                'state': 'failed',
                'note': "Build failed: %s" % str(e)[:500],
            })
            raise

        return snap

    # ==================================================================
    # CORE SQL: populate snapshot lines
    # ==================================================================
    @api.model
    def _sql_populate_snapshot_lines(self, snapshot_id, snapshot_date, our_company_ids):
        """Compute stock qty per (product, warehouse) for our companies
        as of END of snapshot_date (23:59:59), and insert into snapshot_line.

        Uses stock_move_line (source of truth for actual done quantities in Odoo 17).

        Balance = SUM(qty coming into internal locations of warehouse)
                - SUM(qty going out of internal locations of warehouse)
                where date <= end-of-snapshot_date and state = 'done'.

        :returns: number of rows inserted.
        """
        # Cutoff = 23:59:59 of snapshot_date (inclusive full day)
        cutoff_dt = (
            fields.Datetime.to_datetime(snapshot_date)
            + timedelta(days=1)
            - timedelta(seconds=1)
        )

        query = """
            WITH internal_locs AS (
                -- Map every internal location to its owning warehouse.
                -- A location belongs to a warehouse if it sits under the warehouse's
                -- view_location_id (via parent_path prefix match).
                SELECT
                    l.id           AS location_id,
                    l.company_id   AS company_id,
                    w.id           AS warehouse_id
                FROM stock_location l
                JOIN stock_location wvl
                  ON TRUE
                JOIN stock_warehouse w
                  ON w.view_location_id = wvl.id
                 AND l.parent_path LIKE (wvl.parent_path || '%%')
                WHERE l.usage = 'internal'
                  AND l.company_id = ANY(%(company_ids)s)
            ),
            move_in AS (
                -- Stock arriving into any internal location up to cutoff
                SELECT
                    ml.product_id,
                    il.warehouse_id,
                    il.company_id,
                    SUM(ml.quantity) AS qty
                FROM stock_move_line ml
                JOIN internal_locs il
                  ON il.location_id = ml.location_dest_id
                WHERE ml.state = 'done'
                  AND ml.date <= %(cutoff)s
                GROUP BY ml.product_id, il.warehouse_id, il.company_id
            ),
            move_out AS (
                -- Stock leaving any internal location up to cutoff
                SELECT
                    ml.product_id,
                    il.warehouse_id,
                    il.company_id,
                    SUM(ml.quantity) AS qty
                FROM stock_move_line ml
                JOIN internal_locs il
                  ON il.location_id = ml.location_id
                WHERE ml.state = 'done'
                  AND ml.date <= %(cutoff)s
                GROUP BY ml.product_id, il.warehouse_id, il.company_id
            ),
            balances AS (
                SELECT
                    COALESCE(i.product_id,   o.product_id)   AS product_id,
                    COALESCE(i.warehouse_id, o.warehouse_id) AS warehouse_id,
                    COALESCE(i.company_id,   o.company_id)   AS company_id,
                    (COALESCE(i.qty, 0) - COALESCE(o.qty, 0)) AS qty
                FROM move_in i
                FULL OUTER JOIN move_out o
                  ON  i.product_id   = o.product_id
                  AND i.warehouse_id = o.warehouse_id
            )
            INSERT INTO stock_audit_snapshot_line
                (snapshot_id, snapshot_date, product_id, warehouse_id, company_id, qty)
            SELECT
                %(snap_id)s,
                %(snap_date)s,
                product_id,
                warehouse_id,
                company_id,
                qty
            FROM balances
            WHERE qty <> 0
        """

        self.env.cr.execute(query, {
            'company_ids': list(our_company_ids),
            'cutoff':      cutoff_dt,
            'snap_id':     snapshot_id,
            'snap_date':   snapshot_date,
        })
        return self.env.cr.rowcount