from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

AUTO_LOCK_DAYS = 10


class ResCompany(models.Model):
    _inherit = 'res.company'

    invoice_manual_lock_date = fields.Date(
        string="Manual Invoice Lock Date",
        help="Admin-set lock date. Invoices on or before this date are locked.",
    )

    invoice_auto_lock_enabled = fields.Boolean(
        string="Auto-Lock Enabled",
        default=True,
        help=f"If enabled, invoices older than {AUTO_LOCK_DAYS} days "
             f"are automatically locked.",
    )


class AccountMove(models.Model):
    _inherit = 'account.move'

    is_date_locked = fields.Boolean(
        string="Locked",
        compute="_compute_is_date_locked",
        store=False,
    )

    lock_reason = fields.Char(
        string="Lock Reason",
        compute="_compute_is_date_locked",
        store=False,
    )

    @api.depends(
        'invoice_date',
        'date',
        'company_id.invoice_manual_lock_date',
        'company_id.invoice_auto_lock_enabled',
    )
    def _compute_is_date_locked(self):
        today = fields.Date.context_today(self)
        auto_lock_cutoff = today - timedelta(days=AUTO_LOCK_DAYS)

        for move in self:
            if move.move_type not in self._invoice_types():
                move.is_date_locked = False
                move.lock_reason = False
                continue

            move_date = move.invoice_date or move.date
            manual_lock = move.company_id.invoice_manual_lock_date
            auto_enabled = move.company_id.invoice_auto_lock_enabled

            if not move_date:
                move.is_date_locked = False
                move.lock_reason = False
                continue

            if auto_enabled and move_date <= auto_lock_cutoff:
                move.is_date_locked = True
                move.lock_reason = (
                    f"Auto-locked: invoice is older than "
                    f"{AUTO_LOCK_DAYS} days (dated {move_date})."
                )
                continue

            if manual_lock and move_date <= manual_lock:
                move.is_date_locked = True
                move.lock_reason = (
                    f"Manually locked: all invoices on or before "
                    f"{manual_lock} are locked."
                )
                continue

            move.is_date_locked = False
            move.lock_reason = False

    def _invoice_types(self):
        return (
            'out_invoice',
            'out_refund',
            'in_invoice',
            'in_refund',
        )

    def _safe_fields(self):
        return {
            # Chatter fields
            'message_main_attachment_id',
            'message_follower_ids',
            'activity_ids',
            'message_ids',

            # Report / PDF / Send fields
            'invoice_pdf_report_id',
            'invoice_pdf_report_file',
            'is_move_sent',
            'access_token',
            'access_url',
            'access_warning',

            # Email tracking
            'email_cc',

            # Attachment reference updates
            'attachment_ids',
        }

    def _check_lock(self, move, new_vals=None):
        if move.move_type not in self._invoice_types():
            return

        today = fields.Date.context_today(self)
        auto_lock_cutoff = today - timedelta(days=AUTO_LOCK_DAYS)
        manual_lock = move.company_id.invoice_manual_lock_date
        auto_enabled = move.company_id.invoice_auto_lock_enabled
        move_date = move.invoice_date or move.date

        if move_date:
            if auto_enabled and move_date <= auto_lock_cutoff:
                raise UserError(
                    f"INVOICE AUTO-LOCKED\n\n"
                    f"Invoices older than {AUTO_LOCK_DAYS} days are "
                    f"automatically locked.\n"
                    f"Invoice: {move.name or 'New'}\n"
                    f"Invoice Date: {move_date}\n"
                    f"Auto-lock cutoff: {auto_lock_cutoff}"
                )

            if manual_lock and move_date <= manual_lock:
                raise UserError(
                    f"INVOICE LOCKED\n\n"
                    f"Admin has locked all invoices on or before {manual_lock}.\n"
                    f"Invoice: {move.name or 'New'}\n"
                    f"Invoice Date: {move_date}"
                )

        if new_vals:
            new_date = new_vals.get('invoice_date') or new_vals.get('date')
            if new_date:
                if isinstance(new_date, str):
                    new_date = fields.Date.from_string(new_date)

                if auto_enabled and new_date <= auto_lock_cutoff:
                    raise UserError(
                        f"Cannot set invoice date to {new_date}.\n"
                        f"Invoices older than {AUTO_LOCK_DAYS} days are auto-locked."
                    )

                if manual_lock and new_date <= manual_lock:
                    raise UserError(
                        f"Cannot set invoice date to {new_date}.\n"
                        f"Admin has locked all dates on or before {manual_lock}."
                    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for move in records:
            self._check_lock(move)
        return records

    def write(self, vals):
        import logging
        _logger = logging.getLogger(__name__)
        _logger.warning(f"INVOICE LOCK write() called with fields: {list(vals.keys())}")

        if set(vals.keys()).issubset(self._safe_fields()):
            return super().write(vals)

        for move in self:
            self._check_lock(move, new_vals=vals)

        return super().write(vals)

    def unlink(self):
        for move in self:
            self._check_lock(move)
        return super().unlink()

    def button_cancel(self):
        for move in self:
            self._check_lock(move)
        return super().button_cancel()

    def button_draft(self):
        for move in self:
            self._check_lock(move)
        return super().button_draft()


class InvoiceLockAudit(models.Model):
    _name = 'invoice.lock.audit'
    _description = 'Invoice Lock Audit Log'
    _order = 'create_date desc'

    company_id = fields.Many2one(
        'res.company',
        string="Company",
        required=True,
        readonly=True,
    )

    action = fields.Selection(
        [
            ('lock', 'Lock'),
            ('unlock', 'Unlock'),
        ],
        string="Action",
        required=True,
        readonly=True,
    )

    previous_lock_date = fields.Date(
        string="Previous Lock Date",
        readonly=True,
    )

    new_lock_date = fields.Date(
        string="New Lock Date",
        readonly=True,
    )

    user_id = fields.Many2one(
        'res.users',
        string="Performed By",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
