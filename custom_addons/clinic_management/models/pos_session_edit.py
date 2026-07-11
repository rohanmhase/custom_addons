# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup


class PosSessionEdit(models.TransientModel):
    _name = 'pos.session.edit'
    _description = 'POS Session Cash Edit Wizard'

    pos_session_id = fields.Many2one('pos.session', string="Session", required=True)

    action_type = fields.Selection([
        ('edit', 'Edit Existing Entry'),
        ('new', 'Create New Entry')
    ], string='Action', default='new', required=True)

    statement_line_id = fields.Many2one(
        'account.bank.statement.line',
        string='Select Cash Entry to Fix',
        domain="[('pos_session_id', '=', pos_session_id), ('is_replaced', '=', False)]"
    )

    transaction_type = fields.Selection([
        ('in', 'Cash In'),
        ('out', 'Cash Out')
    ], string='Type', default='out', required=True)

    amount = fields.Float(string='Amount', required=True)

    # Mirrors payment_ref on account.bank.statement.line —
    # same field the cashier fills in the standard Cash In/Out popup
    payment_ref = fields.Char(string='Reference')

    @api.onchange('statement_line_id')
    def _onchange_statement_line_id(self):
        if self.statement_line_id:
            raw_amount = self.statement_line_id.amount
            self.transaction_type = 'in' if raw_amount > 0 else 'out'
            self.amount = abs(raw_amount)
            self.payment_ref = self.statement_line_id.payment_ref

    def _get_cash_payment_method(self, session):
        for pm in session.config_id.payment_method_ids:
            if pm.journal_id and pm.journal_id.type == 'cash':
                return pm
        raise UserError(_(
            "No Cash payment method found on POS config '%s'.\nLinked: %s"
        ) % (
            session.config_id.name,
            ', '.join(session.config_id.payment_method_ids.mapped('name'))
        ))

    def _get_receivable_account(self, session, payment_method):
        """
        Proven from Odoo 17 pos_session.py source:
            return (payment_method.receivable_account_id
                    or self.company_id.account_default_pos_receivable_account_id)
        Confirmed from live DB: resolves to 100410 Debtors (PoS)
        """
        return (
            payment_method.receivable_account_id
            or session.company_id.account_default_pos_receivable_account_id
        )

    def _cancel_old_move(self, statement_line):
        """
        Cancels the account_move behind the old statement line.
        - Stays in DB forever (audit trail)
        - State becomes 'cancelled' → excluded from all accounting reports
        - Uses skip_account_move_synchronization context to bypass the
          "exactly one liquidity line" sync validation that previously caused crashes.
        - Checks for strict mode (Lock Posted Entries with Hash) first.
        """
        move = statement_line.move_id
        if not move or move.state == 'cancel':
            return  # already cancelled or no move, nothing to do

        # Check if journal has strict mode (hash lock) — if yes we cannot draft it
        if move.restrict_mode_hash_table:
            raise UserError(_(
                "Cannot cancel the entry for '%s' because journal '%s' has "
                "'Lock Posted Entries with Hash' enabled.\n"
                "Please disable it temporarily under Accounting → Journals → Advanced Settings."
            ) % (statement_line.payment_ref, move.journal_id.name))

        # Use skip_account_move_synchronization to bypass the sync validation
        # that checks "exactly one liquidity line" — proven from Odoo 17 source.
        # Without this context, button_draft() triggers _synchronize_from_moves()
        # which crashes because the ABSL is now marked replaced (inconsistent state).
        move_ctx = move.with_context(skip_account_move_synchronization=True)
        move_ctx.button_draft()
        move_ctx.button_cancel()

    def _create_statement_line(self, session, signed_amount, payment_ref):
        """
        Creates account.bank.statement.line identical to standard POS Cash In/Out.
        Journal entry behind it:
          Line 1 (liquidity)  : journal.default_account_id  e.g. 100118 Cash NAU
          Line 2 (counterpart): receivable account           e.g. 100410 Debtors (PoS)
        Proven from live DB and Odoo 17 source.

        Reference format: POS/00006-ADJ-in-<user text>
        Mirrors standard POS format (POS/00006-in-) but with ADJ to distinguish
        post-closure adjustments from regular cash in/out entries.
        """
        pm = self._get_cash_payment_method(session)
        receivable_account = self._get_receivable_account(session, pm)

        if not pm.journal_id.default_account_id:
            raise UserError(_(
                "Journal '%s' has no Cash Account set."
            ) % pm.journal_id.name)

        if not receivable_account:
            raise UserError(_(
                "No default POS receivable account set on company '%s'."
            ) % session.company_id.name)

        date = session.stop_at.date() if session.stop_at else fields.Date.context_today(self)

        # Build reference: POS/00006-ADJ-in-<user text>
        direction = 'in' if signed_amount >= 0 else 'out'
        full_ref = f"{session.name}-ADJ-{direction}-{payment_ref or ''}"

        return self.env['account.bank.statement.line'].create({
            'journal_id': pm.journal_id.id,
            'pos_session_id': session.id,
            'date': date,
            'payment_ref': full_ref,
            'amount': signed_amount,
            'counterpart_account_id': receivable_account.id,
        })

    def action_apply_adjustment(self):
        self.ensure_one()
        session = self.pos_session_id

        if session.state != 'closed':
            raise UserError(_("This session is not closed."))

        signed_amount = (
            -abs(self.amount) if self.transaction_type == 'out'
            else abs(self.amount)
        )

        if self.action_type == 'edit' and self.statement_line_id:
            old_amount = self.statement_line_id.amount
            old_ref = self.statement_line_id.payment_ref

            # Step 1: cancel the old account_move
            # → stays in DB (audit trail), excluded from accounting reports
            self._cancel_old_move(self.statement_line_id)

            # Step 2: mark old ABSL as replaced
            # → stays in DB, linked to new line for traceability
            # → is_replaced=True means "this line was corrected, ignore it"
            self.statement_line_id.write({'is_replaced': True})

            # Step 3: create the new correct entry
            new_line = self._create_statement_line(session, signed_amount, self.payment_ref)

            # Step 4: only adjust balance by the DIFFERENCE
            # e.g. old=-1000, new=-2000 → difference=-1000 (not -2000)
            difference = signed_amount - old_amount

            session.message_post(
                body=Markup(
                    "<b>Cash Entry Edited (Post-Closure)</b><br/>"
                    "By: <b>{user}</b><br/>"
                    "Reference: {old_ref} → {new_ref}<br/>"
                    "Amount: {old_amount} → {new_amount}<br/>"
                    "Old Statement Line ID: {old_line_id} (cancelled)<br/>"
                    "New Statement Line ID: {new_line_id}"
                ).format(
                    user=self.env.user.name,
                    old_ref=old_ref or '(none)',
                    new_ref=self.payment_ref or '(none)',
                    old_amount=old_amount,
                    new_amount=signed_amount,
                    old_line_id=self.statement_line_id.id,
                    new_line_id=new_line.id,
                )
            )

        else:
            new_line = self._create_statement_line(session, signed_amount, self.payment_ref)
            difference = signed_amount

            session.message_post(
                body=Markup(
                    "<b>Cash Entry Added (Post-Closure)</b><br/>"
                    "By: <b>{user}</b><br/>"
                    "Reference: {ref}<br/>"
                    "Amount: {amount} ({type})<br/>"
                    "Statement Line ID: {line_id}"
                ).format(
                    user=self.env.user.name,
                    ref=self.payment_ref or '(none)',
                    amount=abs(signed_amount),
                    type='Cash In' if signed_amount > 0 else 'Cash Out',
                    line_id=new_line.id,
                )
            )

        session.write({
            'cash_register_balance_end_real': (
                session.cash_register_balance_end_real + difference
            )
        })

        return {'type': 'ir.actions.client', 'tag': 'reload'}


class AccountBankStatementLineExtend(models.Model):
    _inherit = 'account.bank.statement.line'

    is_replaced = fields.Boolean(
        string='Replaced',
        default=False,
        help="If True, this cash entry was corrected post-closure. "
             "The linked journal entry is cancelled. "
             "This line is kept for audit trail only — ignore in calculations."
    )
