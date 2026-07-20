# clinic_warehouse_report.py
from odoo import models, fields, api


class ClinicWarehouseReport(models.TransientModel):
    _name = 'clinic.warehouse.report'
    _description = 'Warehouse Report Dashboard'

    display_name = fields.Char(compute='_compute_display_name')

    report_date = fields.Date(
        string="Report Date",
        default=fields.Date.context_today,
        required=True,
    )

    source_warehouse_id = fields.Many2one(
        'stock.warehouse',
        string="Source Warehouse",
        help="Filter clinics by dispatching warehouse. Leave empty to see all.",
    )

    available_source_warehouse_ids = fields.Many2many(
        'stock.warehouse',
        compute='_compute_available_source_warehouses',
    )

    # Stock counters
    stock_ready_count = fields.Integer(compute='_compute_stock_counters')
    stock_issues_count = fields.Integer(compute='_compute_stock_counters')
    stock_no_response_count = fields.Integer(compute='_compute_stock_counters')
    stock_total_clinics = fields.Integer(compute='_compute_stock_counters')

    # Transfer counters
    transfer_ready_count = fields.Integer(compute='_compute_transfer_counters')
    transfer_issues_count = fields.Integer(compute='_compute_transfer_counters')
    transfer_no_response_count = fields.Integer(compute='_compute_transfer_counters')
    transfer_total_clinics = fields.Integer(compute='_compute_transfer_counters')

    def _compute_display_name(self):
        for rec in self:
            rec.display_name = 'Warehouse Report'

    @api.depends('report_date')
    def _compute_available_source_warehouses(self):
        clinic_wh_ids = self.env['clinic.clinic'].sudo().search([]).mapped('warehouse_id').ids
        real_wh = self.env['stock.warehouse'].search([('id', 'not in', clinic_wh_ids)])
        for rec in self:
            rec.available_source_warehouse_ids = real_wh

    def _get_date_range(self):
        d = self.report_date or fields.Date.context_today(self)
        start = fields.Datetime.to_datetime(f"{d} 00:00:00")
        end = fields.Datetime.to_datetime(f"{d} 23:59:59")
        return start, end

    def _classify_stock_groups(self):
        Confirmation = self.env['clinic.stock.confirmation'].with_context(active_test=False)
        start, end = self._get_date_range()

        domain = [
            ('replenishment_date', '>=', start),
            ('replenishment_date', '<=', end),
            ('replenishment_id', '!=', False),
            ('destination_warehouse_id', '!=', False),
        ]

        # Filter by source warehouse if selected
        if self.source_warehouse_id:
            replenishments = self.env['clinic.stock.replenishment'].sudo().search([
                ('source_warehouse_id', '=', self.source_warehouse_id.id),
            ])
            if not replenishments:
                return {'ready': [], 'issues': [], 'no_response': []}
            domain.append(('replenishment_id', 'in', replenishments.ids))

        all_lines = Confirmation.search(domain)

        groups = {}
        for line in all_lines:
            key = (line.destination_warehouse_id.id, line.replenishment_id.id)
            groups.setdefault(key, []).append(line)

        result = {'ready': [], 'issues': [], 'no_response': []}

        for (clinic_id, rep_id), lines in groups.items():
            confirmed = sum(1 for l in lines if l.state == 'confirmed')
            problems = [l for l in lines if l.is_problem]
            unresolved = [l for l in problems if l.resolution_status == 'unresolved']

            if confirmed == 0:
                category = 'no_response'
            elif unresolved:
                category = 'issues'
            else:
                category = 'ready'

            result[category].append({
                'clinic_id': clinic_id,
                'replenishment_id': rep_id,
                'line_ids': [l.id for l in lines],
                'problem_ids': [l.id for l in problems],
            })
        return result

    def _classify_transfer_groups(self):
        Confirmation = self.env['clinic.internal.transfer.confirmation'].with_context(active_test=False)
        start, end = self._get_date_range()

        domain = [
            ('transfer_date', '>=', start),
            ('transfer_date', '<=', end),
            ('picking_id', '!=', False),
            ('destination_warehouse_id', '!=', False),
        ]

        # Filter by source warehouse if selected
        if self.source_warehouse_id:
            src_loc = self.source_warehouse_id.lot_stock_id.id
            pickings = self.env['stock.picking'].sudo().search([
                ('location_id', '=', src_loc),
            ])
            if not pickings:
                return {'ready': [], 'issues': [], 'no_response': []}
            domain.append(('picking_id', 'in', pickings.ids))

        all_lines = Confirmation.search(domain)

        groups = {}
        for line in all_lines:
            key = (line.destination_warehouse_id.id, line.picking_id.id)
            groups.setdefault(key, []).append(line)

        result = {'ready': [], 'issues': [], 'no_response': []}
        for (clinic_id, pick_id), lines in groups.items():
            confirmed = sum(1 for l in lines if l.state == 'confirmed')
            problems = [l for l in lines if l.is_problem]
            unresolved = [l for l in problems if l.resolution_status == 'unresolved']

            if confirmed == 0:
                category = 'no_response'
            elif unresolved:
                category = 'issues'
            else:
                category = 'ready'

            result[category].append({
                'clinic_id': clinic_id,
                'picking_id': pick_id,
                'line_ids': [l.id for l in lines],
                'problem_ids': [l.id for l in problems],
            })
        return result

    @api.depends('report_date', 'source_warehouse_id')
    def _compute_stock_counters(self):
        for rec in self:
            groups = rec._classify_stock_groups()
            rec.stock_ready_count = len(groups['ready'])
            rec.stock_issues_count = len(groups['issues'])
            rec.stock_no_response_count = len(groups['no_response'])
            rec.stock_total_clinics = (
                rec.stock_ready_count +
                rec.stock_issues_count +
                rec.stock_no_response_count
            )

    @api.depends('report_date', 'source_warehouse_id')
    def _compute_transfer_counters(self):
        for rec in self:
            groups = rec._classify_transfer_groups()
            rec.transfer_ready_count = len(groups['ready'])
            rec.transfer_issues_count = len(groups['issues'])
            rec.transfer_no_response_count = len(groups['no_response'])
            rec.transfer_total_clinics = (
                rec.transfer_ready_count +
                rec.transfer_issues_count +
                rec.transfer_no_response_count
            )

    def _open_stock_summary(self, category, title):
        groups = self._classify_stock_groups().get(category, [])

        Summary = self.env['clinic.warehouse.report.stock.summary']
        summary_ids = []
        for g in groups:
            summary = Summary.create({
                'category': category,
                'report_date': self.report_date,
                'clinic_id': g['clinic_id'],
                'replenishment_id': g['replenishment_id'],
                'line_ids': [(6, 0, g['line_ids'])],
                'problem_line_ids': [(6, 0, g['problem_ids'])],
            })
            summary_ids.append(summary.id)

        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'clinic.warehouse.report.stock.summary',
            'view_mode': 'tree',
            'domain': [('id', 'in', summary_ids)],
            'context': {'create': False, 'delete': False, 'edit': False},
            'target': 'current',
        }

    def _open_transfer_summary(self, category, title):
        groups = self._classify_transfer_groups().get(category, [])

        Summary = self.env['clinic.warehouse.report.transfer.summary']
        summary_ids = []
        for g in groups:
            summary = Summary.create({
                'category': category,
                'report_date': self.report_date,
                'clinic_id': g['clinic_id'],
                'picking_id': g['picking_id'],
                'line_ids': [(6, 0, g['line_ids'])],
                'problem_line_ids': [(6, 0, g['problem_ids'])],
            })
            summary_ids.append(summary.id)

        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'clinic.warehouse.report.transfer.summary',
            'view_mode': 'tree',
            'domain': [('id', 'in', summary_ids)],
            'context': {'create': False, 'delete': False, 'edit': False},
            'target': 'current',
        }

    # Stock buttons
    def action_stock_ready(self):
        return self._open_stock_summary('ready', '✅ Ready to Dispatch')

    def action_stock_issues(self):
        return self._open_stock_summary('issues', '⚠️ Needs Review')

    def action_stock_no_response(self):
        return self._open_stock_summary('no_response', '⏳ No Response')

    # Transfer buttons
    def action_transfer_ready(self):
        return self._open_transfer_summary('ready', '✅ Received OK')

    def action_transfer_issues(self):
        return self._open_transfer_summary('issues', '⚠️ Needs Review')

    def action_transfer_no_response(self):
        return self._open_transfer_summary('no_response', '⏳ No Response')


class ClinicWarehouseReportStockSummary(models.TransientModel):
    _name = 'clinic.warehouse.report.stock.summary'
    _description = 'Warehouse Report - Stock Summary (Level 1)'
    _order = 'clinic_id'

    category = fields.Selection([
        ('ready', 'Ready'),
        ('issues', 'Issues'),
        ('no_response', 'No Response'),
    ], required=True)
    report_date = fields.Date()
    clinic_id = fields.Many2one('stock.warehouse', string="Clinic", required=True)
    replenishment_id = fields.Many2one('clinic.stock.replenishment', string="CSR")
    line_ids = fields.Many2many('clinic.stock.confirmation', 'summary_line_rel',
                                'summary_id', 'line_id', string="All Lines")
    problem_line_ids = fields.Many2many('clinic.stock.confirmation', 'summary_problem_rel',
                                        'summary_id', 'line_id', string="Problem Lines")

    total_items = fields.Integer(compute='_compute_stats', string="Total Items")
    problem_count = fields.Integer(compute='_compute_stats', string="Problems")
    confirmed_count = fields.Integer(compute='_compute_stats', string="Confirmed")

    @api.depends('line_ids', 'problem_line_ids')
    def _compute_stats(self):
        for rec in self:
            rec.total_items = len(rec.line_ids)
            rec.problem_count = len(rec.problem_line_ids)
            rec.confirmed_count = len(rec.line_ids.filtered(lambda l: l.state == 'confirmed'))

    def action_open_lines(self):
        self.ensure_one()

        if self.category == 'issues':
            line_ids = self.problem_line_ids.ids
            title = f"⚠️ {self.clinic_id.name} - Problems"
        else:
            line_ids = self.line_ids.ids
            title = f"{self.clinic_id.name} - {dict(self._fields['category'].selection).get(self.category)}"

        warehouse_tree = self.env.ref(
            'internal_transfer_confirmation.clinic_stock_confirmation_warehouse_tree'
        )
        warehouse_form = self.env.ref(
            'internal_transfer_confirmation.clinic_stock_confirmation_warehouse_form'
        )

        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'clinic.stock.confirmation',
            'views': [
                (warehouse_tree.id, 'list'),
                (warehouse_form.id, 'form'),
            ],
            'view_mode': 'list,form',
            'domain': [('id', 'in', line_ids)],
            'context': {
                'active_test': False,
                'create': False,
                'edit': False,
                'delete': False,
            },
            'target': 'current',
        }


class ClinicWarehouseReportTransferSummary(models.TransientModel):
    _name = 'clinic.warehouse.report.transfer.summary'
    _description = 'Warehouse Report - Transfer Summary (Level 1)'
    _order = 'clinic_id'

    category = fields.Selection([
        ('ready', 'Ready'),
        ('issues', 'Issues'),
        ('no_response', 'No Response'),
    ], required=True)
    report_date = fields.Date()
    clinic_id = fields.Many2one('stock.warehouse', string="Clinic", required=True)
    picking_id = fields.Many2one('stock.picking', string="Transfer")
    line_ids = fields.Many2many('clinic.internal.transfer.confirmation',
                                'transfer_summary_line_rel',
                                'summary_id', 'line_id', string="All Lines")
    problem_line_ids = fields.Many2many('clinic.internal.transfer.confirmation',
                                        'transfer_summary_problem_rel',
                                        'summary_id', 'line_id', string="Problem Lines")

    total_items = fields.Integer(compute='_compute_stats')
    problem_count = fields.Integer(compute='_compute_stats')
    confirmed_count = fields.Integer(compute='_compute_stats')

    @api.depends('line_ids', 'problem_line_ids')
    def _compute_stats(self):
        for rec in self:
            rec.total_items = len(rec.line_ids)
            rec.problem_count = len(rec.problem_line_ids)
            rec.confirmed_count = len(rec.line_ids.filtered(lambda l: l.state == 'confirmed'))

    def action_open_lines(self):
        self.ensure_one()

        if self.category == 'issues':
            line_ids = self.problem_line_ids.ids
            title = f"⚠️ {self.clinic_id.name} - Problems"
        else:
            line_ids = self.line_ids.ids
            title = f"{self.clinic_id.name} - {dict(self._fields['category'].selection).get(self.category)}"

        warehouse_tree = self.env.ref(
            'internal_transfer_confirmation.clinic_transfer_confirmation_warehouse_tree'
        )
        warehouse_form = self.env.ref(
            'internal_transfer_confirmation.clinic_transfer_confirmation_warehouse_form'
        )

        return {
            'type': 'ir.actions.act_window',
            'name': title,
            'res_model': 'clinic.internal.transfer.confirmation',
            'views': [
                (warehouse_tree.id, 'list'),
                (warehouse_form.id, 'form'),
            ],
            'view_mode': 'list,form',
            'domain': [('id', 'in', line_ids)],
            'context': {
                'active_test': False,
                'create': False,
                'edit': False,
                'delete': False,
            },
            'target': 'current',
        }