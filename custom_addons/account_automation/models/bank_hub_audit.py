from odoo import models, fields, api


class BankSalesAudit(models.Model):
    _name = 'bank.sales.audit'
    _description = 'Bank vs HUB Audit'
    _order = 'create_date desc'

    name = fields.Char(required=True)

    bank_config_id = fields.Many2one(
        'bank.statement.parser.config',
        string='Bank',
        required=True
    )

    start_date = fields.Date(required=True)
    end_date = fields.Date(required=True)

    file_name = fields.Char()

    run_by = fields.Many2one(
        'res.users',
        string='Run By',
        default=lambda self: self.env.user,
        readonly=True
    )

    run_date = fields.Datetime(
        string='Run On',
        default=fields.Datetime.now,
        readonly=True
    )

    line_ids = fields.One2many(
        'bank.sales.audit.line',
        'audit_id',
        string='Audit Lines'
    )


class BankSalesAuditLine(models.Model):
    _name = 'bank.sales.audit.line'
    _description = 'Bank vs HUB Audit Line'
    _order = 'difference desc, tid_number asc'

    # FIXED: Odoo can now safely trace 'audit_id.start_date' because BankSalesAudit is defined above
    audit_date = fields.Date(related='audit_id.start_date', string="Audit Date", store=True, readonly=True)

    audit_id = fields.Many2one(
        'bank.sales.audit',
        required=True,
        ondelete='cascade'
    )

    bank_config_id = fields.Many2one(
        related='audit_id.bank_config_id',
        store=True,
        string='Bank'
    )

    tid_number = fields.Char(string='TID')
    clinics_display = fields.Char(string='Clinic(s)')
    system_mode = fields.Char(string='Payment Mode')

    hub_amount = fields.Float(string='HUB Sales', digits=(16, 2))
    bank_amount = fields.Float(string='Bank Receipt', digits=(16, 2))
    difference = fields.Float(string='Variance', digits=(16, 2))

    reason_id = fields.Many2one(
        'bank.sales.audit.reason',
        string='Reason'
    )
    note = fields.Char(string='Note')

    resolution_state = fields.Selection([
        ('open', 'Open'),
        ('resolved', 'Resolved'),
    ], default='open', string='Status')

    net_difference = fields.Float(string='Net Variance', digits=(16, 2))

    created_pending_id = fields.Many2one(
        'bank.sales.audit.pending',
        string='Carried Forward As',
        readonly=True
    )

    settled_pending_ids = fields.One2many(
        'bank.sales.audit.pending',
        'settled_by_line_id',
        string='Settled Against'
    )

    resolved_for_date = fields.Date(
        string="Resolved For Date",
        compute="_compute_resolved_dates",
        help="The past mismatch date that this line is settling."
    )
    resolved_on_date = fields.Date(
        string="Resolved On Date",
        compute="_compute_resolved_dates",
        help="The future audit date where this variance was finally cleared."
    )

    @api.depends('settled_pending_ids', 'created_pending_id.state')
    def _compute_resolved_dates(self):
        for line in self:
            past_entries = line.settled_pending_ids.filtered(lambda p: p.audit_line_id)
            if past_entries and past_entries[0].audit_line_id:
                line.resolved_for_date = past_entries[0].audit_line_id.audit_date
            else:
                line.resolved_for_date = False

            if line.created_pending_id and line.created_pending_id.state == 'settled' and line.created_pending_id.settled_by_line_id:
                line.resolved_on_date = line.created_pending_id.settled_by_line_id.audit_date
            else:
                line.resolved_on_date = False

    def action_open_resolve_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bank.sales.audit.resolve.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_line_id': self.id},
        }


class BankSalesAuditPending(models.Model):
    _name = 'bank.sales.audit.pending'
    _description = 'Bank vs HUB Pending Variance'
    _order = 'create_date asc'

    audit_line_id = fields.Many2one(
        'bank.sales.audit.line',
        string='Origin Line',
        ondelete='cascade',
        required=True
    )
    settled_audit_date = fields.Date(
        related='settled_by_line_id.audit_id.start_date',
        string='Date Resolved',
        store=True,
        readonly=True
    )

    bank_config_id = fields.Many2one(
        'bank.statement.parser.config',
        string='Bank',
        required=True
    )

    tid_number = fields.Char(string='TID')
    clinics_display = fields.Char(string='Clinic(s)')
    system_mode = fields.Char(string='Payment Mode')
    amount = fields.Float(string='Pending Amount', digits=(16, 2))

    reason_id = fields.Many2one('bank.sales.audit.reason', string='Reason')
    note = fields.Char(string='Note')

    state = fields.Selection([
        ('open', 'Open'),
        ('settled', 'Settled'),
    ], default='open', string='Status')

    settled_by_line_id = fields.Many2one(
        'bank.sales.audit.line',
        string='Settled By Line'
    )
    settled_in_audit_id = fields.Many2one(
        'bank.sales.audit',
        string='Settled Via Audit Link',
        compute='_compute_settled_in_audit_id',
        store=True,
        readonly=True
    )

    @api.depends('settled_by_line_id.audit_id')
    def _compute_settled_in_audit_id(self):
        for record in self:
            if record.settled_by_line_id and record.settled_by_line_id.audit_id:
                record.settled_in_audit_id = record.settled_by_line_id.audit_id.id
            else:
                record.settled_in_audit_id = False