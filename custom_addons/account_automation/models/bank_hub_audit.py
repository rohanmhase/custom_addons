from odoo import models, fields, api
from odoo.exceptions import AccessError
from markupsafe import Markup  # Safe HTML escaping compilation for Odoo 17


class BankSalesAudit(models.Model):
    _name = 'bank.sales.audit'
    _description = 'Bank vs HUB Audit'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(required=True, tracking=True)
    bank_config_id = fields.Many2one(
        'bank.statement.parser.config',
        string='Bank',
        required=True,
        tracking=True
    )
    start_date = fields.Date(required=True, tracking=True)
    end_date = fields.Date(required=True, tracking=True)
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

    # NOTE: "Reset Entire Audit" header button removed from the form view
    # per request — resetting is now done per-line (see
    # BankSalesAuditLine.action_reset_line_to_open / Reset button on each
    # row). This method is left in place in case a bulk/batch reset is
    # ever needed again, but nothing in the UI calls it anymore.
    def action_reset_to_open(self):
        """ Reverses the resolution logic completely, restoring balances. """
        for audit in self:
            for line in audit.line_ids:
                if line.resolution_state == 'open':
                    continue
                line.action_reset_line_to_open()

            audit.message_post(
                body="<strong>Audit Reset:</strong> All lines inside this audit document have been reset back to 'Open' status.",
                subtype_xmlid="mail.mt_note"
            )


class BankSalesAuditLine(models.Model):
    _name = 'bank.sales.audit.line'
    _description = 'Bank vs HUB Audit Line'
    _order = 'difference desc, tid_number asc'

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
    net_difference = fields.Float(string='Remaining Gap', digits=(16, 2))
    created_pending_id = fields.Many2one(
        'bank.sales.audit.pending',
        string='Remaining Gap Entry',
        readonly=True
    )
    pending_status = fields.Selection(
        related='created_pending_id.state',
        string='Remaining Gap Status',
        readonly=True
    )
    settled_pending_ids = fields.One2many(
        'bank.sales.audit.pending',
        'settled_by_line_id',
        string='Settled Against'
    )
    resolved_for_date = fields.Date(
        string="Gap From Date",
        compute="_compute_resolved_dates"
    )
    resolved_on_date = fields.Date(
        string="Cleared On Date",
        compute="_compute_resolved_dates"
    )

    def action_reset_line_to_open(self):
        """ Reverses the resolution logic for a single line item row only. """
        for line in self:
            if line.resolution_state == 'open' and not line.reason_id:
                continue

            if line.created_pending_id:
                target_pending = line.created_pending_id
                line.write({'created_pending_id': False})
                if target_pending.state == 'open':
                    target_pending.unlink()
                elif target_pending.state == 'settled':
                    raise ValueError(
                        f"Cannot reset TID {line.tid_number or 'N/A'}. The pending balance entry generated "
                        f"by this line has already been settled downstream in a future audit session."
                    )

            if line.settled_pending_ids:
                line.settled_pending_ids.write({
                    'state': 'open',
                    'settled_by_line_id': False,
                })

            line.write({
                'reason_id': False,
                'note': False,
                'resolution_state': 'open',
                'net_difference': 0.0,
            })

            log_body = Markup(
                f"<strong>Line Reset:</strong> Row item for TID <code>{line.tid_number or 'N/A'}</code> "
                f"({line.system_mode or ''}) has been individually reset back to open."
            )

            if line.audit_id:
                line.audit_id.message_post(
                    body=log_body,
                    subtype_xmlid="mail.mt_note"
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

    active = fields.Boolean(default=True, string="Active")

    def action_archive_record(self):
        self.write({'active': False})

    def action_permanent_purge(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError("Only administrators can purge historical audit entries.")
        for record in self:
            record.write({'active': False})