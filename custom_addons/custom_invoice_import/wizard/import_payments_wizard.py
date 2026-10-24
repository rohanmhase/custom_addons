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
        'utr': [
            'utr', 'utr number', 'transaction id', 'reference number'
        ],
        'delivery_date': [
            'delivery date', 'delivered date', 'delivered on'
        ],
    }

    def _parse_date(self, raw_value):
        """Accept common CSV date formats and return YYYY-MM-DD string."""
        if not raw_value:
            return False

        value = str(raw_value).strip()

        # already correct
        known_formats = (
            '%Y-%m-%d',  # 2026-08-15
            '%d-%m-%Y',  # 15-08-2026
            '%d/%m/%Y',  # 15/08/2026
            '%m/%d/%Y',  # 8/15/2026  (your current CSV)
            '%d.%m.%Y',  # 15.08.2026
            '%Y/%m/%d',  # 2026/08/15
            '%d-%m-%y',  # 15-08-26
            '%m/%d/%y',  # 8/15/26
            '%d/%m/%y',  # 15/08/26
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

        config = self.env['online.import.config'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1) or self.env['online.import.config'].search([], limit=1)

        if not config or not config.pod_journal_id:
            raise UserError(_("Please set the POD Bank Journal in Invoicing -> Configuration -> Online Imports."))

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

        if 'order_number' not in col_index:
            raise UserError(_("Missing required column: Order Number"))

        # order_number -> payment_date(YYYY-MM-DD)
        payment_data = {}
        errors = []

        for line_no, row in enumerate(rows[1:], start=2):
            if not row or not any(str(c or '').strip() for c in row):
                continue

            order_no = str(row[col_index['order_number']] or '').strip()
            if not order_no:
                errors.append("Line %s: Order Number is empty" % line_no)
                continue

            def get_val(key):
                idx = col_index.get(key)
                return str(row[idx]).strip() if idx is not None and idx < len(row) else ''

            pay_raw = get_val('payment_date')
            del_raw = get_val('delivery_date')
            utr_val = get_val('utr')

            try:
                pay_date = self._parse_date(pay_raw) if pay_raw else False
                del_date = self._parse_date(del_raw) if del_raw else False
            except UserError as e:
                errors.append("Line %s: %s" % (line_no, e.args[0]))
                continue

            payment_data[order_no] = {
                'payment_date': pay_date,
                'delivery_date': del_date,
                'utr': utr_val
            }

        if errors:
            raise UserError(_("Payment CSV validation failed:\n") + "\n".join(errors[:30]))

        if not payment_data:
            raise UserError(_("No valid order/date rows found in CSV."))

        # Removed payment_state filter so we can update already-paid orders with UTR
        moves = self.env['account.move'].search([
            '|',
            ('ref', 'in', list(payment_data.keys())),
            ('invoice_origin', 'in', list(payment_data.keys())),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
        ])

        processed_move_ids = []
        skipped = []

        for move in moves:
            order_no = move.ref if move.ref in payment_data else move.invoice_origin
            p_data = payment_data.get(order_no)
            if not p_data: continue

            # 1. Update Tracking Table
            online_order = self.env['online.order'].search([('name', '=', order_no)], limit=1)
            tracker_vals = {}
            if p_data['utr']: tracker_vals['utr'] = p_data['utr']
            if p_data['delivery_date']: tracker_vals['delivery_date'] = p_data['delivery_date']

            if online_order:
                if tracker_vals: online_order.write(tracker_vals)
            else:
                tracker_vals.update({'name': order_no, 'move_id': move.id, 'payment_method': 'pod'})
                self.env['online.order'].create(tracker_vals)

            # 2. Register Payment ONLY if invoice is unpaid
            if move.payment_state in ['not_paid', 'partial']:
                if not p_data['payment_date']:
                    skipped.append("%s: Unpaid but missing Payment Date in CSV." % order_no)
                    continue
                try:
                    pay_wizard = self.env['account.payment.register'].with_company(move.company_id).with_context(
                        active_model='account.move',
                        active_ids=move.ids,
                        default_company_id=move.company_id.id,
                    ).create({
                        'journal_id': config.pod_journal_id.id,
                        'payment_date': p_data['payment_date'],
                    })
                    pay_wizard.action_create_payments()
                    processed_move_ids.append(move.id)
                except Exception as e:
                    skipped.append("%s: %s" % (order_no, str(e)))
            else:
                processed_move_ids.append(move.id)

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
