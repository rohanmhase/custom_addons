from odoo import models, fields
import re

class AccountMove(models.Model):
    _inherit = 'account.move'

    def _must_check_constrains_date_sequence(self):
        # Skip date/sequence validation for our custom prefixed invoices
        prefix = self.company_id.invoice_prefix
        if prefix and self.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
            return False
        return super()._must_check_constrains_date_sequence()

    def _get_starting_sequence(self):
        self.ensure_one()
        prefix = self.company_id.invoice_prefix
        if prefix and self.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
            today = fields.Date.today()
            if self.move_type in ('out_refund', 'in_refund'):
                return "R%sINV/%04d/%02d/0000" % (prefix, today.year, today.month)
            return "%sINV/%04d/%02d/0000" % (prefix, today.year, today.month)
        return super()._get_starting_sequence()

    def _set_next_sequence(self):
        if self.state != 'posted':
            return
        super()._set_next_sequence()
        prefix = self.company_id.invoice_prefix
        if not prefix or self.move_type not in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund'):
            return
        if not self.name or self.name == '/':
            return

        today = fields.Date.today()
        if self.move_type in ('out_refund', 'in_refund'):
            correct_prefix = "R%sINV/%04d/%02d/" % (prefix, today.year, today.month)
        else:
            correct_prefix = "%sINV/%04d/%02d/" % (prefix, today.year, today.month)

        current_id = self._origin.id
        domain = [
            ('company_id', '=', self.company_id.id),
            ('move_type', '=', self.move_type),
            ('sequence_prefix', '=', correct_prefix),
        ]
        if isinstance(current_id, int) and current_id:
            domain.append(('id', '!=', current_id))

        last_move = self.env['account.move'].sudo().search(
            domain, order='sequence_number desc', limit=1
        )

        next_number = (last_move.sequence_number + 1) if last_move else 1

        self.name = "%s%04d" % (correct_prefix, next_number)
        self.sequence_prefix = correct_prefix
        self.sequence_number = next_number