import base64
import csv
import io
import json
import re
from datetime import datetime

from odoo import models, fields
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools import html_escape

ADMIN_GROUP = 'mybillbook_invoices.group_mybillbook_admin'


class MybillbookImportWizard(models.TransientModel):
    _name = 'mybillbook.import.wizard'
    _description = 'Import My Bill Book Invoices'

    file = fields.Binary('Upload CSV File')
    file_name = fields.Char('File Name')

    state = fields.Selection([
        ('upload', 'Upload'),
        ('validated', 'Validated'),
        ('error', 'Error'),
    ], default='upload')

    validation_message = fields.Html('Validation Result', readonly=True, sanitize=False)
    missing_clinics = fields.Text('Missing Clinics', readonly=True)
    total_invoices = fields.Integer('Invoices Found', readonly=True)
    total_lines = fields.Integer('Lines Found', readonly=True)
    can_import = fields.Boolean('Can Import', default=False)
    can_override = fields.Boolean('Can Override', default=False)

    parsed_data = fields.Text('Parsed Data JSON')

    # ------------------------------------------------------------------ helpers
    def _parse_date(self, date_str):
        if not date_str:
            return False
        date_str = str(date_str).strip()
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%m/%d/%y', '%d-%m-%y'):
            try:
                return datetime.strptime(date_str, fmt).date().isoformat()
            except ValueError:
                continue
        return False

    def _parse_qty(self, qty_str):
        if not qty_str:
            return 0.0
        match = re.search(r'[\d.]+', str(qty_str))
        return float(match.group()) if match else 0.0

    def _to_float(self, value):
        try:
            return float(str(value).replace(',', '').strip() or 0)
        except (ValueError, TypeError):
            return 0.0

    def _read_csv(self):
        if not self.file:
            raise UserError("Please upload a CSV file first.")
        try:
            raw = base64.b64decode(self.file)
            text = raw.decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
            if not rows:
                raise UserError("CSV is empty.")
            return rows
        except UserError:
            raise
        except Exception as e:
            raise UserError(f"Cannot read CSV: {e}")

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'mybillbook.import.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _active_clinic_names(self, names):
        Clinic = self.env['mybillbook.clinic']
        return set(Clinic.search([('name', 'in', list(names)), ('active', '=', True)]).mapped('name'))

    def _check_admin(self):
        if not self.env.user.has_group(ADMIN_GROUP):
            raise AccessError("Only a My Bill Book Administrator can import CSV files.")

    @staticmethod
    def _norm_name(s):
        return ' '.join((s or '').split()).casefold()

    @staticmethod
    def _is_number(value):
        try:
            float(str(value).replace(',', '').strip())
            return True
        except (ValueError, TypeError):
            return False

    def _db_party_conflicts(self, invoices_dict):
        """Same clinic + same invoice no already in DB (active) but with a different party."""
        Invoice = self.env['mybillbook.invoice']
        out = []
        for inv in invoices_dict.values():
            ex = Invoice.search([
                ('invoice_no', '=', inv['invoice_no']),
                ('clinic_name', '=', inv['clinic_name']),
                ('active', '=', True),
            ], limit=1)
            if ex and self._norm_name(ex.party_name) != self._norm_name(inv['party_name']):
                out.append("%s / Invoice %s: file has '%s' but system already has '%s'" % (
                    inv['clinic_name'], inv['invoice_no'], inv['party_name'], ex.party_name))
        return out

    # ------------------------------------------------------------------ validate
    def action_validate(self):
        """Parse CSV and check clinics. Does NOT create anything."""
        self.ensure_one()
        self._check_admin()
        rows = self._read_csv()

        invoices_dict = {}
        clinics_in_csv = set()
        line_count = 0
        errors = []

        for i, row in enumerate(rows, start=2):  # row 1 = header
            inv_no = (row.get('Invoice No') or '').strip()
            clinic = (row.get('Clinic') or '').strip()
            party = (row.get('Party Name') or '').strip()
            date_raw = (row.get('Date') or '').strip()
            mobile = (row.get('Mobile') or row.get('Party Mobile') or row.get('Phone') or '').strip()

            if not inv_no:
                errors.append(f"Row {i}: missing Invoice No")
                continue
            if not clinic:
                errors.append(f"Row {i}: missing Clinic (Invoice {inv_no})")
                continue
            if not party:
                errors.append(f"Row {i}: missing Party Name (Invoice {inv_no})")
            if not date_raw:
                errors.append(f"Row {i}: missing Date (Invoice {inv_no})")
            elif not self._parse_date(date_raw):
                errors.append(f"Row {i}: invalid Date '{date_raw}' (Invoice {inv_no})")
            if not (row.get('Item Name') or '').strip():
                errors.append(f"Row {i}: missing Item Name (Invoice {inv_no})")
            if not self._is_number(row.get('Amount')):
                errors.append(f"Row {i}: Amount '{row.get('Amount')}' is not a number (Invoice {inv_no})")
            if (row.get('Price/Unit') or '').strip() and not self._is_number(row.get('Price/Unit')):
                errors.append(f"Row {i}: Price/Unit '{row.get('Price/Unit')}' is not a number (Invoice {inv_no})")
            if mobile and not re.fullmatch(r'\d{1,10}', mobile):
                errors.append(f"Row {i}: Mobile '{mobile}' must be digits only, max 10 digits (Invoice {inv_no})")

            clinics_in_csv.add(clinic)
            key = f"{clinic}||{inv_no}||{self._norm_name(party)}"

            if key not in invoices_dict:
                invoices_dict[key] = {
                    'invoice_no': inv_no,
                    'date': self._parse_date(date_raw),
                    'party_name': party,
                    'party_mobile': mobile,
                    'payment_mode': (row.get('MODE') or row.get('Mode') or '').strip(),
                    'clinic_name': clinic,
                    'notes': (row.get('Notes') or '').strip(),
                    'lines': [],
                }

            invoices_dict[key]['lines'].append({
                'item_name': (row.get('Item Name') or '').strip(),
                'quantity_str': (row.get('Quantity') or '').strip(),
                'quantity_num': self._parse_qty(row.get('Quantity')),
                'rate': self._to_float(row.get('Price/Unit')),
                'amount': self._to_float(row.get('Amount')),
            })
            line_count += 1


        existing = self._active_clinic_names(clinics_in_csv)
        missing = sorted(clinics_in_csv - existing)

        html = []
        html.append(f"<p><b>CSV rows read:</b> {len(rows)}</p>")
        html.append(f"<p><b>Unique invoices:</b> {len(invoices_dict)}</p>")
        html.append(f"<p><b>Line items:</b> {line_count}</p>")
        html.append(f"<p><b>Clinics in file:</b> {len(clinics_in_csv)}</p>")

        if existing:
            html.append("<p style='color:green;'><b>Clinics matched (" + str(len(existing)) + "):</b></p><ul>")
            for c in sorted(existing):
                html.append(f"<li>{html_escape(c)}</li>")
            html.append("</ul>")

        if missing:
            html.append("<p style='color:#b00;'><b>IMPORT BLOCKED - clinics not configured (" + str(
                len(missing)) + "):</b></p><ul>")
            for c in missing:
                html.append(f"<li>{html_escape(c)}</li>")
            html.append("</ul>")
            html.append(
                "<p>Create these under <b>Configuration -> Clinics</b> with the exact same name, then validate again. "
                "Administrators can use the override button in an emergency.</p>")
        elif clinics_in_csv:
            html.append("<p style='color:green;'><b>All clinics match.</b></p>")

        if conflicts:
            html.append(
                "<p style='color:#b00;'><b>IMPORT BLOCKED - same invoice number with a different party name (" + str(
                    len(conflicts)) + "). "
                                      "This cannot be overridden, fix the CSV:</b></p><ul>")
            for c in conflicts[:30]:
                html.append(f"<li>{html_escape(c)}</li>")
            if len(conflicts) > 30:
                html.append(f"<li>... and {len(conflicts) - 30} more</li>")
            html.append("</ul>")

        if errors:
            html.append("<p style='color:#b00;'><b>Row errors (fix the CSV, import is blocked):</b></p><ul>")
            for e in errors[:30]:
                html.append(f"<li>{html_escape(e)}</li>")
            if len(errors) > 30:
                html.append(f"<li>... and {len(errors) - 30} more</li>")
            html.append("</ul>")

        structurally_ok = bool(invoices_dict) and not errors
        can_import = structurally_ok and not missing
        can_override = structurally_ok and bool(missing)

        self.write({
            'state': 'validated' if can_import else 'error',
            'validation_message': ''.join(html),
            'missing_clinics': '\n'.join(missing) if missing else '',
            'total_invoices': len(invoices_dict),
            'total_lines': line_count,
            'can_import': can_import,
            'can_override': can_override,
            'parsed_data': json.dumps(invoices_dict) if structurally_ok else '',
        })
        return self._reopen()

    # ------------------------------------------------------------------ import
    def action_import(self):
        return self._do_import(override=False)

    def action_import_override(self):
        """Emergency only: import even if clinics are not configured."""
        self._check_admin()
        return self._do_import(override=True)

    def _do_import(self, override):
        self.ensure_one()
        self._check_admin()
        allowed = self.can_import or (override and self.can_override)
        if not allowed or not self.parsed_data:
            raise ValidationError("Please Validate the CSV first. Import is blocked until validation succeeds.")

        invoices_dict = json.loads(self.parsed_data)


        # Re-check clinics at import time (they may have changed since validation)
        names = {inv['clinic_name'] for inv in invoices_dict.values()}
        missing = sorted(names - self._active_clinic_names(names))
        if missing and not override:
            raise UserError("Import blocked. These clinics are not configured: " + ", ".join(missing))

        Invoice = self.env['mybillbook.invoice']
        created, skipped = 0, 0
        for inv in invoices_dict.values():
            # Only ACTIVE invoices count as duplicates. Archived / deleted ones are ignored.
            exists = Invoice.search([
                ('invoice_no', '=', inv['invoice_no']),
                ('clinic_name', '=', inv['clinic_name']),
                ('party_name', '=', inv['party_name']),
                ('active', '=', True),
            ], limit=1)
            if exists:
                upd = {}
                if inv.get('party_mobile') and not exists.party_mobile:
                    upd['party_mobile'] = inv['party_mobile']
                if inv.get('notes') and not exists.notes:
                    upd['notes'] = inv['notes']
                if upd:
                    exists.sudo().write(upd)
                skipped += 1
                continue

            lines = [(0, 0, {
                'item_name': l['item_name'],
                'quantity_str': l['quantity_str'],
                'quantity_num': l['quantity_num'],
                'rate': l['rate'],
                'amount': l['amount'],
            }) for l in inv['lines']]

            Invoice.create({
                'invoice_no': inv['invoice_no'],
                'date': inv['date'] or False,
                'party_name': inv['party_name'],
                'party_mobile': inv.get('party_mobile') or False,
                'payment_mode': inv['payment_mode'],
                'clinic_name': inv['clinic_name'],
                'notes': inv['notes'],
                'line_ids': lines,
            })
            created += 1

        msg = f"Created: {created} invoices | Skipped duplicates: {skipped} | Lines: {self.total_lines}"
        if override and missing:
            msg += f" | Imported with unconfigured clinics: {', '.join(missing)}"
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'My Bill Book Import',
                'message': msg,
                'type': 'success',
                'sticky': bool(override and missing),
                'next': {'type': 'ir.actions.act_window_close'},
            }
        }

    def action_reset(self):
        self.write({
            'state': 'upload',
            'file': False,
            'file_name': False,
            'validation_message': False,
            'missing_clinics': False,
            'total_invoices': 0,
            'total_lines': 0,
            'can_import': False,
            'can_override': False,
            'parsed_data': False,
        })
        return self._reopen()
