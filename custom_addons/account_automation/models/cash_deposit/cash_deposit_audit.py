from odoo import models, fields, api
from odoo.exceptions import AccessError, UserError
from markupsafe import Markup


class CashDepositAudit(models.Model):
    _name = 'cash.deposit.audit'
    _description = 'Cash Deposit vs POS Cash-Out Audit'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'chatter.toggle.mixin']

    name = fields.Char(required=True, tracking=True)
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
        'cash.deposit.audit.line',
        'audit_id',
        string='Audit Lines'
    )

    # ── 3-Level Soft Delete Fields ──
    active = fields.Boolean(default=True, string="Active")
    is_purged = fields.Boolean(
        default=False,
        string="Purged from UI",
        help="When True the record is hidden from all views but still exists in the database. "
             "Only an Admin can permanently delete it."
    )

    def unlink(self):
        """
        3-Level Delete Protection:
          Level 1  (active=True)  → Archive the record  (active=False)
          Level 2  (active=False, is_purged=False) → Purge from UI (is_purged=True)
          Level 3  (is_purged=True) → Real DB delete, Admin only
        """
        to_archive = self.env['cash.deposit.audit']
        to_purge = self.env['cash.deposit.audit']
        to_delete = self.env['cash.deposit.audit']

        for record in self:
            if record.active and not record.is_purged:
                to_archive |= record
            elif not record.active and not record.is_purged:
                to_purge |= record
            else:
                to_delete |= record

        # Level 1: Archive
        if to_archive:
            to_archive.write({'active': False})
            for rec in to_archive:
                rec.message_post(
                    body="<strong>Archived:</strong> This audit has been moved to the archive.",
                    subtype_xmlid="mail.mt_note"
                )

        # Level 2: Purge from UI (still in DB)
        if to_purge:
            if not self.env.user.has_group('account_automation.group_account_automation_admin'):
                raise AccessError(
                    "Only Account Automation Admins can purge archived audit records from the UI."
                )
            to_purge.write({'is_purged': True})
            for rec in to_purge:
                rec.message_post(
                    body="<strong>Purged from UI:</strong> This audit is now hidden from all views "
                         "but retained in the database for compliance.",
                    subtype_xmlid="mail.mt_note"
                )

        # Level 3: Real DB unlink — Admin only
        if to_delete:
            if not self.env.user.has_group('account_automation.group_account_automation_admin'):
                raise AccessError(
                    "Only Account Automation Admins can permanently delete purged audit records."
                )
            # Cascade-delete lines and pending entries first
            to_delete.mapped('line_ids').unlink()
            super(CashDepositAudit, to_delete).unlink()

        return True

    def action_restore_from_archive(self):
        """Restore an archived record back to active state."""
        for record in self:
            if not record.active and not record.is_purged:
                record.write({'active': True})
                record.message_post(
                    body="<strong>Restored:</strong> This audit has been restored from the archive.",
                    subtype_xmlid="mail.mt_note"
                )

    def action_restore_from_purge(self):
        """Restore a purged record back to archived state (visible in archive)."""
        if not self.env.user.has_group('account_automation.group_account_automation_admin'):
            raise AccessError("Only Admins can restore purged records.")
        for record in self:
            if record.is_purged:
                record.write({'is_purged': False, 'active': False})
                record.message_post(
                    body="<strong>Restored from Purge:</strong> This audit is now visible in the archive again.",
                    subtype_xmlid="mail.mt_note"
                )


class CashDepositAuditLine(models.Model):
    _name = 'cash.deposit.audit.line'
    _description = 'Cash Deposit Audit Line'
    _order = 'audit_date desc, abs_difference desc'

    audit_id = fields.Many2one(
        'cash.deposit.audit',
        required=True,
        ondelete='cascade'
    )
    audit_date = fields.Date(
        string='Deposit Date',
        required=True
    )
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic')
    clinic_display = fields.Char(string='Clinic')

    pos_cash_in = fields.Float(string='POS Cash In', digits=(16, 2))
    pos_cash_out = fields.Float(string='POS Cash Out', digits=(16, 2))
    total_expected_deposit = fields.Float(
        string='Total Cash (Out - In)',
        digits=(16, 2),
        help="Net cash the clinic should have deposited: Cash Out minus Cash In."
    )
    bank_received = fields.Float(string='Bank Received', digits=(16, 2))
    difference = fields.Float(string='Variance', digits=(16, 2))
    abs_difference = fields.Float(
        string='Abs Variance',
        compute='_compute_abs_difference',
        store=True
    )

    responsible_person = fields.Char(string='Responsible Person')

    reason_id = fields.Many2one('cash.deposit.audit.reason', string='Reason')
    note = fields.Char(string='Note')
    resolution_state = fields.Selection([
        ('open', 'Open'),
        ('resolved', 'Resolved'),
    ], default='open', string='Status')

    net_difference = fields.Float(string='Remaining Gap', digits=(16, 2))
    created_pending_id = fields.Many2one(
        'cash.deposit.pending',
        string='Remaining Gap Entry',
        readonly=True
    )
    pending_status = fields.Selection(
        related='created_pending_id.state',
        string='Remaining Gap Status',
        readonly=True
    )
    settled_pending_ids = fields.One2many(
        'cash.deposit.pending',
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

    @api.depends('difference')
    def _compute_abs_difference(self):
        for line in self:
            line.abs_difference = abs(line.difference)

    @api.depends('settled_pending_ids', 'created_pending_id.state')
    def _compute_resolved_dates(self):
        for line in self:
            past_entries = line.settled_pending_ids.filtered(lambda p: p.audit_line_id)
            if past_entries and past_entries[0].audit_line_id:
                line.resolved_for_date = past_entries[0].audit_line_id.audit_date
            else:
                line.resolved_for_date = False

            if (line.created_pending_id and
                    line.created_pending_id.state == 'settled' and
                    line.created_pending_id.settled_by_line_id):
                line.resolved_on_date = line.created_pending_id.settled_by_line_id.audit_date
            else:
                line.resolved_on_date = False

    def action_open_resolve_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'cash.deposit.resolve.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_line_id': self.id},
        }

    def action_reset_line_to_open(self):
        for line in self:
            if line.resolution_state == 'open' and not line.reason_id:
                continue

            if line.created_pending_id:
                target_pending = line.created_pending_id
                line.write({'created_pending_id': False})
                if target_pending.state == 'open':
                    target_pending.unlink()
                elif target_pending.state == 'settled':
                    raise UserError(
                        f"Cannot reset line for {line.clinic_display or 'N/A'}.\n\n"
                        f"The pending gap entry generated by this line has already been "
                        f"settled downstream in a future audit session."
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
                f"<strong>Line Reset:</strong> Cash Deposit line for "
                f"<code>{line.clinic_display or 'N/A'}</code> on "
                f"<code>{line.audit_date}</code> has been reset to Open."
            )
            if line.audit_id:
                line.audit_id.message_post(
                    body=log_body,
                    subtype_xmlid="mail.mt_note"
                )


class CashDepositPending(models.Model):
    _name = 'cash.deposit.pending'
    _description = 'Cash Deposit Pending Variance'
    _order = 'create_date asc'

    audit_line_id = fields.Many2one(
        'cash.deposit.audit.line',
        string='Origin Line',
        ondelete='cascade',
        required=True
    )
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic')
    clinic_display = fields.Char(string='Clinic')
    amount = fields.Float(string='Pending Amount', digits=(16, 2))
    reason_id = fields.Many2one('cash.deposit.audit.reason', string='Reason')
    note = fields.Char(string='Note')
    state = fields.Selection([
        ('open', 'Open'),
        ('settled', 'Settled'),
    ], default='open', string='Status')
    settled_by_line_id = fields.Many2one(
        'cash.deposit.audit.line',
        string='Settled By Line'
    )
    settled_audit_date = fields.Date(
        related='settled_by_line_id.audit_id.start_date',
        string='Date Resolved',
        store=True,
        readonly=True
    )
    active = fields.Boolean(default=True)

    def action_archive_record(self):
        self.write({'active': False})

    def action_permanent_purge(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError("Only administrators can purge pending cash deposit entries.")
        self.write({'active': False})   