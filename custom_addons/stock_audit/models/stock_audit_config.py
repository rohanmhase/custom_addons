# -*- coding: utf-8 -*-
import logging
from odoo import models, fields, api
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class StockAuditConfig(models.Model):
    """Singleton configuration for Stock Audit module.

    Stores which ROOT companies are considered 'ours'.
    All descendants (via res_company.parent_id) are automatically included.
    Companies not listed here (and not descendants) are excluded — typically
    franchise or unrelated tenants sharing the same Odoo instance.
    """
    _name = 'stock.audit.config'
    _description = 'Stock Audit Configuration'
    _rec_name = 'display_name'

    display_name = fields.Char(
        compute='_compute_display_name',
        store=False,
    )
    root_company_ids = fields.Many2many(
        comodel_name='res.company',
        relation='stock_audit_config_company_rel',
        column1='config_id',
        column2='company_id',
        string='Root Companies',
        help="Root/parent companies to include in audit.\n"
             "All descendant companies (via parent_id) are automatically included.\n"
             "Do NOT add franchise or unrelated companies here."
    )
    resolved_company_ids = fields.Many2many(
        comodel_name='res.company',
        relation='stock_audit_config_resolved_company_rel',
        column1='config_id',
        column2='company_id',
        string='Included Companies (Resolved)',
        compute='_compute_resolved_company_ids',
        store=False,
        help="Read-only preview of ALL companies (roots + descendants) "
             "that will be included in the audit."
    )
    resolved_company_count = fields.Integer(
        string='Total Companies Included',
        compute='_compute_resolved_company_ids',
        store=False,
    )
    note = fields.Text(
        string='Notes',
        default=(
            "Add your MAIN/HQ companies here.\n"
            "All their child companies (branches, clinics) are automatically included "
            "via the parent_id hierarchy.\n\n"
            "Do NOT add franchise or unrelated companies — leave them out to exclude "
            "their stock from audit."
        ),
    )

    # ------------------------------------------------------------------
    # COMPUTES
    # ------------------------------------------------------------------
    @api.depends('root_company_ids')
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = "Stock Audit Configuration"

    @api.depends('root_company_ids')
    def _compute_resolved_company_ids(self):
        """Show admin the full resolved list (roots + all descendants)
        as a live preview when they edit root_company_ids.
        """
        for rec in self:
            if not rec.root_company_ids:
                rec.resolved_company_ids = False
                rec.resolved_company_count = 0
                continue
            company_ids = rec._resolve_descendants(rec.root_company_ids.ids)
            rec.resolved_company_ids = [(6, 0, company_ids)]
            rec.resolved_company_count = len(company_ids)

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------
    @api.model
    def get_config(self):
        """Return the singleton config record. Creates one if missing."""
        config = self.sudo().search([], limit=1)
        if not config:
            config = self.sudo().create({})
        return config

    @api.model
    def get_our_company_ids(self):
        """Return list of ALL company IDs considered 'ours' (roots + descendants).
        Fast: single recursive SQL query.
        Returns [] if no roots configured.
        """
        config = self.get_config()
        if not config.root_company_ids:
            return []
        return self._resolve_descendants(config.root_company_ids.ids)

    # ------------------------------------------------------------------
    # INTERNAL
    # ------------------------------------------------------------------
    @api.model
    def _resolve_descendants(self, root_ids):
        """Given a list of root company IDs, return roots + all descendants
        using recursive CTE on res_company.parent_id.
        """
        if not root_ids:
            return []
        self.env.cr.execute("""
            WITH RECURSIVE company_tree AS (
                SELECT id
                FROM res_company
                WHERE id = ANY(%s)
                UNION
                SELECT c.id
                FROM res_company c
                JOIN company_tree t ON c.parent_id = t.id
            )
            SELECT id FROM company_tree
        """, (list(root_ids),))
        return [r[0] for r in self.env.cr.fetchall()]

    # ------------------------------------------------------------------
    # CONSTRAINTS
    # ------------------------------------------------------------------
    @api.constrains('root_company_ids')
    def _check_no_child_as_root(self):
        """Warn if a company added as 'root' actually has a parent itself.
        This is not fatal (it will still work), but likely a config mistake.
        """
        for rec in self:
            bad = rec.root_company_ids.filtered(lambda c: c.parent_id)
            if bad:
                names = ", ".join(bad.mapped('name'))
                raise ValidationError(
                    "The following companies have a parent company and should NOT be "
                    "listed as ROOT companies (their parent will handle them):\n\n%s\n\n"
                    "Please add only top-level parent companies here."
                    % names
                )

    # ------------------------------------------------------------------
    # SINGLETON ENFORCEMENT
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Prevent creating multiple config records; only one singleton allowed."""
        existing = self.sudo().search([], limit=1)
        if existing:
            # Return existing; ignore new create attempts
            _logger.info(
                "[Stock Audit] Attempted to create second config record. "
                "Returning existing singleton (id=%s).", existing.id,
            )
            return existing
        return super().create(vals_list)

    def unlink(self):
        """Prevent deletion of the singleton config."""
        raise ValidationError(
            "The Stock Audit Configuration cannot be deleted. "
            "You may clear its fields instead."
        )