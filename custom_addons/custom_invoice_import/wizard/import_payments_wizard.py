import base64
import csv
import io
from datetime import datetime

from odoo import models, fields, _
from odoo.exceptions import UserError


class ImportPaymentsWizard(models.TransientModel):
    _name = 'import.payments.wizard'
    _description = 'Import POD Payments Wizard'

    file = fields.Binary(string='Payment CSV File', required=True)
    file_name = fields.Char(string='File Name')

    HEADER_MAP = {
        'order_number': [
            'order number', 'salesorder number', 'so number',
            'order_number', 'ref', 'source document'
        ],
        'payment_date': [
            'payment date', 'payment_date', 'date', 'paid date'
        ],
    }

    def _parse_date(self, raw_value):
        """Accept common CSV date formats and return YYYY-MM-DD string."""
        if not raw_value:
            return False

        value = str(raw_value).strip()

        # already correct
        known_formats = (
            '%Y-%m-%d',   # 2026-08-15
            '%d-%m-%Y',   # 15-08-2026
            '%d/%m/%Y',   # 15/08/2026
            '%m/%d/%Y',   # 8/15/2026  (your current CSV)
            '%d.%m.%Y',   # 15.08.2026
            '%Y/%m/%d',   # 2026/08/15
            '%d-%m-%y',   # 15-08-26
            '%m/%d/%y',   # 8/15/26
            '%d/%m/%y',   # 15/08/26
        )

        for fmt in known_formats:
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue

        # Excel sometimes gives datetime-like strings with time
        for fmt in ('%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%d/%m/%Y %H:%M:%S'):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue

        raise UserError(_(
            "Invalid payment date '%s'.\n"
            "Accepted formats: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, DD-MM-YYYY."
        ) % value)

    def action_import_payments(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_("Please upload a Payment CSV file."))

        try:
            raw = base64.b64decode(self.file)
            text = raw.decode('utf-8-sig')
        except Exception:
            raise UserError(_("Cannot read file. Please upload a valid UTF-8 CSV."))

        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            raise UserError(_("CSV file is empty."))

        raw_headers = [str(h or '').strip() for h in rows[0]]
        lower_headers = [h.lower() for h in raw_headers]
        col_index = {}

        for key, aliases in self.HEADER_MAP.items():
            for alias in aliases:
                if alias in lower_headers:
                    col_index[key] = lower_headers.index(alias)
                    break

        missing = [k for k in ('order_number', 'payment_date') if k not in col_index]
        if missing:
            raise UserError(_(
                "Missing required column(s): %s\nFound headers: %s"
            ) % (', '.join(missing), ', '.join([h for h in raw_headers if h])))

        # order_number -> payment_date(YYYY-MM-DD)
        payment_data = {}
        errors = []

        for line_no, row in enumerate(rows[1:], start=2):
            if not row or not any(str(c or '').strip() for c in row):
                continue

            order_no = str(row[col_index['order_number']] or '').strip()
            pay_raw = str(row[col_index['payment_date']] or '').strip()

            if not order_no:
                errors.append("Line %s: Order Number is empty" % line_no)
                continue
            if not pay_raw:
                errors.append("Line %s: Payment Date is empty" % line_no)
                continue

            try:
                pay_date = self._parse_date(pay_raw)
            except UserError as e:
                errors.append("Line %s: %s" % (line_no, e.args[0]))
                continue

            payment_data[order_no] = pay_date

        if errors:
            raise UserError(_("Payment CSV validation failed:\n") + "\n".join(errors[:30]))

        if not payment_data:
            raise UserError(_("No valid order/date rows found in CSV."))

        moves = self.env['account.move'].search([
            '|',
            ('ref', 'in', list(payment_data.keys())),
            ('invoice_origin', 'in', list(payment_data.keys())),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ['not_paid', 'partial']),
        ])

        if not moves:
            raise UserError(_(
                "No unpaid posted invoices found matching the Order Numbers in the CSV."
            ))

        bank_journal = self.env['account.journal'].search([('type', '=', 'bank')], limit=1)
        if not bank_journal:
            raise UserError(_("No Bank Journal found to register payments."))

        processed_move_ids = []
        skipped = []

        for move in moves:
            pay_date = payment_data.get(move.ref) or payment_data.get(move.invoice_origin)
            if not pay_date:
                continue

            try:
                pay_wizard = self.env['account.payment.register'].with_context(
                    active_model='account.move',
                    active_ids=move.ids,
                ).create({
                    'journal_id': bank_journal.id,
                    'payment_date': pay_date,
                })
                pay_wizard.action_create_payments()
                processed_move_ids.append(move.id)
            except Exception as e:
                skipped.append("%s: %s" % (move.ref or move.invoice_origin or move.name, str(e)))

        if not processed_move_ids:
            msg = _("No payments were created.")
            if skipped:
                msg += "\n" + "\n".join(skipped[:20])
            raise UserError(msg)

        # Optional: show skipped info in logs only; main UI shows paid invoices
        return {
            'name': _('Processed Paid Invoices'),
            'type': 'ir.actions.act_window',
            'view_mode': 'tree,form',
            'res_model': 'account.move',
            'domain': [('id', 'in', processed_move_ids)],
            'target': 'current',
        }