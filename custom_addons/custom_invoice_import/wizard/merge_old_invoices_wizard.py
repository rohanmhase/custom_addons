import logging
from odoo import models, fields, Command, _
from odoo.exceptions import UserError
_logger = logging.getLogger(__name__)

class MergeOldInvoicesWizard(models.TransientModel):
    _name = 'merge.old.invoices.wizard'
    _description = 'Merge Old Tele-consultation Invoices'

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
    ], default='draft')
    summary = fields.Text(readonly=True)

    def _get_candidates(self):
        """Tele-Medicine customers only (partner nature = online_sales), not already migrated."""
        done_ids = self.env['teleconsult.invoice.line'].sudo().search([]).mapped('move_id').ids
        return self.env['account.move'].search([
            ('move_type', '=', 'out_invoice'),
            ('partner_id.nature_id.code', '=', 'online_sales'),
            ('invoice_date', '>=', self.date_from),
            ('invoice_date', '<=', self.date_to),
            ('id', 'not in', done_ids),
        ])

    def action_preview(self):
        self.ensure_one()
        moves = self._get_candidates()
        lines = ["%s | %s | %s | %.2f | %s" % (
            m.name or m.ref or m.id, m.invoice_date,
            m.partner_id.name or '', m.amount_total, m.payment_state,
        ) for m in moves[:300]]
        extra = "\n... +%s more" % (len(moves) - 300) if len(moves) > 300 else ''
        self.summary = _("Candidates: %s invoices") % len(moves) \
                       + "\n\nNumber | Date | Customer | Total | Payment\n" \
                       + '\n'.join(lines) + extra
        return self._reload()

    def action_merge(self):
        self.ensure_one()
        config = self.env['online.import.config'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1) or self.env['online.import.config'].search([], limit=1)

        if not config or not config.merged_product_id:
            raise UserError(_("Set Combined Product (Pre-Split) in Online Imports Configuration first."))

        moves = self._get_candidates()
        if not moves:
            raise UserError(_("No candidate invoices found."))

        cr = self.env.cr
        ok, errors, merged_ids = 0, [], []
        config_id = config.id
        labels = {m.id: (m.name or m.ref or str(m.id)) for m in moves}
        move_ids = moves.ids[:50]  # batch cap to avoid worker timeout
        cr.commit()

        for move_id in move_ids:
            try:
                self._merge_one(
                    self.env['account.move'].browse(move_id),
                    self.env['online.import.config'].browse(config_id),
                    self.env['teleconsult.invoice.line'],
                )
                self.env.flush_all()
                cr.commit()
                ok += 1
                merged_ids.append(move_id)
            except Exception as e:
                cr.rollback()
                self.env.invalidate_all()
                _logger.exception("Merge failed for %s", labels.get(move_id))
                errors.append("%s: %s: %s" % (labels.get(move_id), type(e).__name__, e))

        if merged_ids:
            return {
                'name': _('Merged Invoices (%s merged, %s failed)') % (ok, len(errors)),
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'view_mode': 'tree,form',
                'domain': [('id', 'in', merged_ids)],
                'target': 'current',
            }
        self.state = 'done'
        self.summary = _("Merged: %s\nFailed: %s\n%s") % (
            ok, len(errors), '\n'.join(errors[:30])
        )
        return self._reload()

    def _merge_one(self, move, config, TeleLine):
        orig_total = move.amount_total
        was_posted = move.state == 'posted'
        pay_moves = self.env['account.move']

        if was_posted:
            recv = move.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable'
            )
            for aml in recv:
                for m in aml.matched_debit_ids:
                    pay_moves |= m.debit_move_id.move_id
                for m in aml.matched_credit_ids:
                    pay_moves |= m.credit_move_id.move_id
            partials = recv.matched_debit_ids | recv.matched_credit_ids
            if partials:
                partials.unlink()
            move.button_draft()

        product_lines = move.invoice_line_ids.filtered(lambda l: l.display_type == 'product')
        if not product_lines:
            raise UserError(_("No product lines"))

        total_untaxed = 0.0
        total_incl = 0.0
        tax_ids = []
        tax_pct = 0.0
        tele_vals = []

        for pl in product_lines:
            sub_base = pl.quantity * pl.price_unit
            if pl.discount:
                sub_base *= (1.0 - pl.discount / 100.0)

            line_tax_pct = 0.0
            if pl.tax_ids:
                tax_ids = pl.tax_ids.ids
                line_tax_pct = sum(t.amount for t in pl.tax_ids if t.amount_type == 'percent')
                tax_pct = line_tax_pct

            sub_incl = sub_base * (1.0 + line_tax_pct / 100.0)
            total_untaxed += sub_base
            total_incl += sub_incl

            label = pl.name or (pl.product_id.display_name if pl.product_id else 'Item')
            tele_vals.append({
                'move_id': move.id,
                'product_name': label,
                'quantity': pl.quantity,
            })

        # Set totals outside loop based on original invoice totals
        total_incl = orig_total
        total_untaxed = move.amount_untaxed or (total_incl / (1.0 + tax_pct / 100.0) if tax_pct else total_incl)

        split_date = config.split_start_date or fields.Date.from_string('2026-11-01')
        inv_date = move.invoice_date or fields.Date.context_today(self)
        lines_cmd = []

        if inv_date >= split_date and config.consultation_product_id:
            consult_product = config.consultation_product_id
            consult_std = config.consultation_amount or 600.0
            min_med_incl = config.min_medicine_amount or 50.0

            if total_incl >= (consult_std + min_med_incl):
                consult_price = consult_std
                consult_disc = 0.0
                med_target_incl = total_incl - consult_std
            else:
                med_target_incl = min_med_incl
                consult_target_net = max(total_incl - min_med_incl, 0.0)
                consult_price = consult_std
                consult_disc = ((consult_std - consult_target_net) / consult_std) * 100.0 if consult_std else 0.0

            med_base_price = med_target_incl / (1.0 + tax_pct / 100.0) if tax_pct else med_target_incl

            lines_cmd.append(Command.create({
                'product_id': consult_product.id,
                'quantity': 1.0,
                'price_unit': round(consult_price, 2),
                'discount': round(consult_disc, 2),
                'tax_ids': [],
            }))
            if not config.medicine_product_id:
                raise UserError(_("Set Medicine Product in Online Imports Configuration first."))
            lines_cmd.append(Command.create({
                'product_id': config.medicine_product_id.id,
                'quantity': 1.0,
                'price_unit': round(med_base_price, 2),
                'discount': 0.0,
                'tax_ids': [Command.set(tax_ids)] if tax_ids else [],
            }))
        else:
            lines_cmd.append(Command.create({
                'product_id': config.merged_product_id.id,
                'quantity': 1.0,
                'price_unit': round(total_untaxed, 2),
                'discount': 0.0,
                'tax_ids': [Command.set(tax_ids)] if tax_ids else [],
            }))

        product_lines.unlink()
        move.write({
            'narration': False,
            'invoice_line_ids': lines_cmd,
        })

        if was_posted:
            move.action_post()
            if pay_moves:
                inv_recv = move.line_ids.filtered(
                    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled
                )
                pay_recv = pay_moves.mapped('line_ids').filtered(
                    lambda l: l.account_id.account_type == 'asset_receivable' and not l.reconciled
                )
                if inv_recv and pay_recv:
                    (inv_recv | pay_recv).reconcile()

        TeleLine.create(tele_vals)

    def _reload(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }