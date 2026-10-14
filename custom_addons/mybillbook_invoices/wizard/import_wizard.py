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
        # Works for '1', '1 PC', '1 NONE', '2.5 KG', '1,000 PCS' -> only the leading number is used.
        match = re.search(r'\d[\d,]*(?:\.\d+)?|\.\d+', str(qty_str))
        if not match:
            return 0.0
        try:
            return float(match.group().replace(',', ''))
        except ValueError:
            return 0.0

    @staticmethod
    def _calc_discount(rate, qty, amount):
        """Auto discount = (rate x qty) - amount, only when positive. Returns (discount, discount_pct)."""
        if qty <= 0 or rate <= 0:
            return 0.0, 0.0
        gross = rate * qty
        disc = round(gross - amount, 2)
        if disc < 0.01:
            return 0.0, 0.0
        return disc, round(disc / gross * 100, 2)

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

    def _existing_invoices(self, invoices_dict):
        """One query per clinic: {(clinic, invoice_no): record} for ACTIVE invoices already in DB."""
        by_clinic = {}
        for inv in invoices_dict.values():
            by_clinic.setdefault(inv['clinic_name'], set()).add(inv['invoice_no'])
        Invoice = self.env['mybillbook.invoice']
        found = {}
        for clinic, nos in by_clinic.items():
            for ex in Invoice.search_read(
                    [('clinic_name', '=', clinic), ('invoice_no', 'in', list(nos)), ('active', '=', True)],
                    ['invoice_no', 'party_name', 'party_mobile', 'notes']):
                found.setdefault((clinic, ex['invoice_no']), ex)
        return found

    def _db_party_conflicts(self, invoices_dict, existing=None):
        if existing is None:
            existing = self._existing_invoices(invoices_dict)
        out = []
        for inv in invoices_dict.values():
            ex = existing.get((inv['clinic_name'], inv['invoice_no']))
            if ex and self._norm_name(ex['party_name']) != self._norm_name(inv['party_name']):
                out.append("%s / Invoice %s: file has '%s' but system already has '%s'" % (
                    inv['clinic_name'], inv['invoice_no'], inv['party_name'], ex['party_name']))
        return out

    # ------------------------------------------------------------------ validate
    def action_validate(self):
        """Parse CSV and check clinics. Does NOT create anything."""
        self.ensure_one()
        self._check_admin()
        rows = self._read_csv()

        invoices_dict = {}
        party_variants = {}  # key -> {normalized party: original party}
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
            key = f"{clinic}||{inv_no}"
            party_variants.setdefault(key, {}).setdefault(self._norm_name(party), party)

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

            qty_num = self._parse_qty(row.get('Quantity'))
            rate_val = self._to_float(row.get('Price/Unit'))
            amount_val = self._to_float(row.get('Amount'))
            disc, disc_pct = self._calc_discount(rate_val, qty_num, amount_val)
            invoices_dict[key]['lines'].append({
                'item_name': (row.get('Item Name') or '').strip(),
                'quantity_str': (row.get('Quantity') or '').strip(),
                'quantity_num': qty_num,
                'rate': rate_val,
                'amount': amount_val,
                'discount': disc,
                'discount_pct': disc_pct,
            })
            line_count += 1

        conflicts = []
        for key, variants in party_variants.items():
            if len(variants) > 1:
                clinic, inv_no = key.split('||', 1)
                conflicts.append("%s / Invoice %s: used for different parties -> %s" % (
                    clinic, inv_no, ' | '.join("'%s'" % v for v in variants.values())))
        conflicts += self._db_party_conflicts(invoices_dict)

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
                "<p style='color:#b00;'><b>IMPORT BLOCKED - same invoice number with a different party name ("
                + str(len(conflicts)) + "). This cannot be overridden, fix the CSV:</b></p><ul>")
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

        structurally_ok = bool(invoices_dict) and not errors and not conflicts
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
        existing = self._existing_invoices(invoices_dict)
        conflicts = self._db_party_conflicts(invoices_dict, existing)
        if conflicts:
            raise ValidationError("Import blocked. Same invoice number with a different party name:\n"
                                  + "\n".join(conflicts[:10]))

        # Re-check clinics at import time (they may have changed since validation)
        names = {inv['clinic_name'] for inv in invoices_dict.values()}
        missing = sorted(names - self._active_clinic_names(names))
        if missing and not override:
            raise UserError("Import blocked. These clinics are not configured: " + ", ".join(missing))

        Invoice = self.env['mybillbook.invoice']
        skipped = 0
        to_create = []
        for inv in invoices_dict.values():
            ex = existing.get((inv['clinic_name'], inv['invoice_no']))
            if ex:
                upd = {}
                if inv.get('party_mobile') and not ex['party_mobile']:
                    upd['party_mobile'] = inv['party_mobile']
                if inv.get('notes') and not ex['notes']:
                    upd['notes'] = inv['notes']
                if upd:
                    Invoice.browse(ex['id']).sudo().write(upd)
                skipped += 1
                continue
            to_create.append({
                'invoice_no': inv['invoice_no'],
                'date': inv['date'] or False,
                'party_name': inv['party_name'],
                'party_mobile': inv.get('party_mobile') or False,
                'payment_mode': inv['payment_mode'],
                'clinic_name': inv['clinic_name'],
                'notes': inv['notes'],
                'line_ids': [(0, 0, {
                    'item_name': l['item_name'],
                    'quantity_str': l['quantity_str'],
                    'quantity_num': l['quantity_num'],
                    'rate': l['rate'],
                    'amount': l['amount'],
                    'discount': l.get('discount', 0.0),
                    'discount_pct': l.get('discount_pct', 0.0),
                }) for l in inv['lines']],
            })
        for k in range(0, len(to_create), 500):
            Invoice.create(to_create[k:k + 500])
        created = len(to_create)

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
