from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError
from markupsafe import Markup


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _loader_params_product_product(self):
        # 1. Get the standard fields (name, price, etc.)
        result = super()._loader_params_product_product()

        # 2. Add 'type' so the JS patch can actually see it
        result['search_params']['fields'].append('type')

        return result

    def _get_cash_payment_method(self):
        for pm in self.config_id.payment_method_ids:
            if pm.journal_id and pm.journal_id.type == 'cash':
                return pm
        raise UserError(_(
            "No Cash payment method found on POS config '%s'.\nLinked: %s"
        ) % (self.config_id.name,
             ', '.join(self.config_id.payment_method_ids.mapped('name'))))

    def action_force_post_closure_cash_adjustment(self, amount, payment_ref, transaction_type='out'):
        self.ensure_one()

        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_("Only System Administrators can alter closed session financials."))
        if self.state != 'closed':
            raise UserError(_("This tool is only for already closed sessions."))

        signed_amount = -abs(amount) if transaction_type == 'out' else abs(amount)

        pm = self._get_cash_payment_method()
        receivable_account = (
                pm.receivable_account_id
                or self.company_id.account_default_pos_receivable_account_id
        )

        if not pm.journal_id.default_account_id:
            raise UserError(_("Journal '%s' has no Cash Account set.") % pm.journal_id.name)
        if not receivable_account:
            raise UserError(_("No default POS receivable account on company '%s'.") % self.company_id.name)

        date = self.stop_at.date() if self.stop_at else fields.Date.context_today(self)

        # Build reference: POS/00006-ADJ-in-<user text>
        direction = 'in' if signed_amount >= 0 else 'out'
        full_ref = f"{self.name}-ADJ-{direction}-{payment_ref or ''}"

        st_line = self.env['account.bank.statement.line'].create({
            'journal_id': pm.journal_id.id,
            'pos_session_id': self.id,
            'date': date,
            'payment_ref': full_ref,
            'amount': signed_amount,
            'counterpart_account_id': receivable_account.id,
        })

        self.write({
            'cash_register_balance_end_real': self.cash_register_balance_end_real + signed_amount
        })

        self.message_post(
            body=Markup(
                "<b>Post-Closure Cash Adjustment</b><br/>"
                "By: <b>{user}</b><br/>"
                "Reference: {ref}<br/>"
                "Amount: {amount} ({type})<br/>"
                "Journal: {journal} | Line ID: {line_id}"
            ).format(
                user=self.env.user.name,
                ref=full_ref,
                amount=abs(signed_amount),
                type='Cash In' if signed_amount > 0 else 'Cash Out',
                journal=pm.journal_id.name,
                line_id=st_line.id,
            )
        )
        return True
