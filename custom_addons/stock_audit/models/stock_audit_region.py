# -*- coding: utf-8 -*-
from odoo import models, fields, api
from odoo.exceptions import ValidationError


class StockAuditRegion(models.Model):
    _name = 'stock.audit.region'
    _description = 'Stock Audit Region'
    _order = 'name'

    name = fields.Char(
        string='Region Name',
        required=True,
        help="Unique name for this region (e.g., Mumbai, Delhi, Pune)."
    )
    warehouse_ids = fields.Many2many(
        comodel_name='stock.warehouse',
        relation='stock_audit_region_warehouse_rel',
        column1='region_id',
        column2='warehouse_id',
        string='Warehouses',
        help="Warehouses/clinics that belong to this region. "
             "Audit run for this region will include only these warehouses."
    )
    warehouse_count = fields.Integer(
        string='Warehouse Count',
        compute='_compute_warehouse_count',
        store=False,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'name_unique',
            'UNIQUE(name)',
            'Region name must be unique.'
        ),
    ]

    @api.depends('warehouse_ids')
    def _compute_warehouse_count(self):
        for rec in self:
            rec.warehouse_count = len(rec.warehouse_ids)

    @api.constrains('name')
    def _check_name_not_empty(self):
        for rec in self:
            if not rec.name or not rec.name.strip():
                raise ValidationError("Region name cannot be empty or whitespace only.")