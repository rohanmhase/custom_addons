# -*- coding: utf-8 -*-

import base64
import csv
import hashlib
import io
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

COL_PRODUCT = 'product'
COL_QTY = 'quantity'
COL_PRICE = 'unit price'
COL_TAXES = 'taxes'
COL_DESC = 'description'

REQUIRED_COLUMNS = {COL_PRODUCT, COL_QTY, COL_PRICE}


class ImportInvoiceLinesWizard(models.TransientModel):
    _name = 'import.invoice.lines.wizard'
    _description = 'Import Invoice Lines from CSV'

    csv_file = fields.Binary(string='CSV File')
    csv_filename = fields.Char(string='File Name')
    invoice_id = fields.Many2one(
        comodel_name='account.move',
        string='Invoice',
        readonly=True,
    )
    test_passed = fields.Boolean(
        string='Test Passed',
        default=False,
        readonly=True,
    )
    test_result_html = fields.Html(
        string='Test Result',
        readonly=True,
        sanitize=False,
    )
    tested_file_hash = fields.Char(
        string='Tested File Hash',
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_id = self.env.context.get('active_id')
        active_model = self.env.context.get('active_model', 'account.move')
        if active_id and active_model == 'account.move':
            invoice = self.env['account.move'].browse(active_id)
            if invoice.exists():
                res['invoice_id'] = invoice.id
            else:
                raise UserError(_('The invoice referenced does not exist.'))
        else:
            raise UserError(_('This wizard must be opened from an invoice form view.'))
        return res

    @api.onchange('csv_file')
    def _onchange_csv_file(self):
        """When user changes the file, reset the test state."""
        for wizard in self:
            wizard.test_passed = False
            wizard.test_result_html = False
            wizard.tested_file_hash = False

    def action_download_template(self):
        """Download the sample CSV template. No file upload required."""
        url = '/invoice_line_importer/static/description/sample_template.csv'
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'self',
        }

    def action_test_import(self):
        """Parse and validate CSV WITHOUT creating any lines."""
        self.ensure_one()

        self.write({
            'test_passed': False,
            'test_result_html': False,
            'tested_file_hash': False,
        })

        if not self.csv_file:
            raise UserError(_('Please upload a CSV file before testing.'))

        try:
            rows, normalised_headers = self._read_and_parse_csv()
        except UserError as e:
            self.test_result_html = self._build_error_html([str(e.args[0])])
            return self._reopen_wizard()

        row_errors = []
        valid_rows = []
        for row_index, raw_row in enumerate(rows, start=2):
            try:
                line_vals = self._process_csv_row(
                    raw_row=raw_row,
                    normalised_headers=normalised_headers,
                    row_number=row_index,
                    invoice=self.invoice_id,
                )
                valid_rows.append((row_index, line_vals))
            except UserError as row_error:
                row_errors.append(str(row_error.args[0]))

        if row_errors:
            self.test_result_html = self._build_error_html(row_errors)
            return self._reopen_wizard()

        if not valid_rows:
            self.test_result_html = self._build_error_html(
                [_('No valid data rows found in the CSV file.')]
            )
            return self._reopen_wizard()

        self.write({
            'test_passed': True,
            'tested_file_hash': self._compute_file_hash(self.csv_file),
            'test_result_html': self._build_success_html(valid_rows),
        })
        return self._reopen_wizard()

    def action_import_lines(self):
        self.ensure_one()

        if not self.csv_file:
            raise UserError(_('Please upload a CSV file before importing.'))

        if not self.test_passed:
            raise UserError(_('You must click "Test File" first and see a success message before importing.'))

        current_hash = self._compute_file_hash(self.csv_file)
        if current_hash != self.tested_file_hash:
            self.write({
                'test_passed': False,
                'test_result_html': False,
                'tested_file_hash': False,
            })
            raise UserError(_('The CSV file has changed since the last test. Please click "Test File" again.'))

        invoice = self.invoice_id
        if not invoice.exists():
            raise UserError(_('The target invoice no longer exists.'))

        if invoice.state != 'draft':
            raise UserError(
                _('Invoice lines can only be imported into a Draft invoice. Current state: %s') % invoice.state
            )

        rows, normalised_headers = self._read_and_parse_csv()

        line_commands = []
        row_errors = []
        for row_index, raw_row in enumerate(rows, start=2):
            try:
                line_vals = self._process_csv_row(
                    raw_row=raw_row,
                    normalised_headers=normalised_headers,
                    row_number=row_index,
                    invoice=invoice,
                )
                line_commands.append((0, 0, line_vals))
            except UserError as row_error:
                row_errors.append(str(row_error.args[0]))

        if row_errors:
            raise UserError(
                _('Import failed due to the following error(s):\n\n%s') % '\n'.join(row_errors)
            )

        if not line_commands:
            raise UserError(_('No valid lines found in the CSV file to import.'))

        try:
            invoice.write({'invoice_line_ids': line_commands})
        except Exception as exc:
            _logger.exception('invoice_line_importer: Failed to write lines to invoice %s', invoice.name)
            raise UserError(
                _('Failed to save imported lines to the invoice.\nTechnical detail: %s') % str(exc)
            ) from exc

        imported_count = len(line_commands)
        _logger.info(
            'invoice_line_importer: Imported %d line(s) into invoice %s.',
            imported_count, invoice.name,
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Successful'),
                'message': _('%d line(s) imported successfully into invoice %s.') % (imported_count, invoice.name or ''),
                'type': 'success',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _reopen_wizard(self):
        """Reopen the same wizard record so the user sees test results."""
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    @staticmethod
    def _compute_file_hash(binary_data):
        """Return an MD5 hash of the file content for change detection."""
        try:
            raw = base64.b64decode(binary_data)
        except Exception:
            return False
        return hashlib.md5(raw).hexdigest()

    def _read_and_parse_csv(self):
        """Decode + parse CSV. Returns (rows, normalised_headers)."""
        try:
            raw_bytes = base64.b64decode(self.csv_file)
        except Exception as exc:
            raise UserError(_('Failed to decode the uploaded file. Please re-upload.')) from exc

        try:
            csv_text = raw_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                csv_text = raw_bytes.decode('latin-1')
            except Exception as exc:
                raise UserError(_('Unable to read the CSV file. Please save it as UTF-8 and re-upload.')) from exc

        if not csv_text.strip():
            raise UserError(_('The uploaded CSV file is empty.'))

        csv_stream = io.StringIO(csv_text)
        reader = csv.DictReader(csv_stream)

        if reader.fieldnames is None:
            raise UserError(_('The CSV file is empty or has no header row.'))

        normalised_headers = {h.strip().lower(): h for h in reader.fieldnames}

        missing = REQUIRED_COLUMNS - set(normalised_headers.keys())
        if missing:
            raise UserError(
                _('Missing required column(s) in CSV header: %s\nExpected columns (case-insensitive): Product, Quantity, Unit Price, Taxes, Description')
                % ', '.join(sorted(missing))
            )

        rows = list(reader)
        if not rows:
            raise UserError(_('The CSV file contains a header row but no data rows.'))
        return rows, normalised_headers

    def _process_csv_row(self, raw_row, normalised_headers, row_number, invoice):

        def get_cell(col_key):
            original_header = normalised_headers.get(col_key)
            if original_header is None:
                return ''
            value = raw_row.get(original_header, '') or ''
            return value.strip()

        product_name_raw = get_cell(COL_PRODUCT)
        if not product_name_raw:
            raise UserError(_("Row %d: 'Product' cell is empty.") % row_number)
        product = self._find_product(product_name_raw, row_number, invoice)

        qty_raw = get_cell(COL_QTY)
        if not qty_raw:
            raise UserError(_("Row %d: 'Quantity' cell is empty.") % row_number)
        quantity = self._parse_float(qty_raw, 'Quantity', row_number)

        price_raw = get_cell(COL_PRICE)
        if not price_raw:
            raise UserError(_("Row %d: 'Unit Price' cell is empty.") % row_number)
        price_unit = self._parse_float(price_raw, 'Unit Price', row_number)

        taxes_raw = get_cell(COL_TAXES)
        tax_ids = self._find_taxes(taxes_raw, row_number, invoice)

        description_raw = get_cell(COL_DESC)
        name = description_raw if description_raw else product.name

        line_vals = {
            'product_id': product.id,
            'quantity': quantity,
            'price_unit': price_unit,
            'name': name,
        }
        if taxes_raw:
            line_vals['tax_ids'] = [(6, 0, tax_ids.ids)]
        return line_vals

    def _find_product(self, product_name_raw, row_number, invoice):
        """
        STRICT product lookup: EXACT match only (case-insensitive).
        Searches default_code first, then name. Never creates products.
        """
        company_id = invoice.company_id.id
        Product = self.env['product.product']

        base_domain = [
            ('active', '=', True),
            '|',
            ('company_id', '=', False),
            ('company_id', '=', company_id),
        ]

        # 1. EXACT match on internal reference
        product = Product.search(
            base_domain + [('default_code', '=ilike', product_name_raw)],
            limit=1,
        )
        if product:
            return product

        # 2. EXACT match on product name
        product = Product.search(
            base_domain + [('name', '=ilike', product_name_raw)],
            limit=1,
        )
        if product:
            return product

        # NOT FOUND - raise error, do NOT create
        raise UserError(
            _("Row %(row)d: Product '%(name)s' not found. The product name or internal reference must match EXACTLY (case-insensitive) with an existing product in Odoo. No new products will be created by this import.")
            % {'row': row_number, 'name': product_name_raw}
        )

    def _find_taxes(self, taxes_raw, row_number, invoice):
        """
        STRICT tax lookup: EXACT match only (case-insensitive).
        Never creates taxes.
        """
        AccountTax = self.env['account.tax']
        found_taxes = AccountTax.browse()

        if not taxes_raw:
            return found_taxes

        # Split on pipe (|) to avoid CSV quoting issues with commas inside cells
        tax_names = [t.strip() for t in taxes_raw.split('|') if t.strip()]

        move_type = invoice.move_type
        if move_type in ('out_invoice', 'out_refund'):
            type_tax_use = 'sale'
        elif move_type in ('in_invoice', 'in_refund'):
            type_tax_use = 'purchase'
        else:
            type_tax_use = 'none'

        company_id = invoice.company_id.id

        for tax_name in tax_names:
            tax = AccountTax.search(
                [
                    ('name', '=ilike', tax_name),
                    ('type_tax_use', 'in', [type_tax_use, 'none']),
                    ('active', '=', True),
                    '|',
                    ('company_id', '=', False),
                    ('company_id', '=', company_id),
                ],
                limit=1,
            )
            if not tax:
                # Look for the tax regardless of type for better error message
                tax_any = AccountTax.search(
                    [('name', '=ilike', tax_name), ('active', '=', True)],
                    limit=1,
                )
                if tax_any:
                    raise UserError(
                        _("Row %(row)d: Tax '%(tax)s' exists but is not applicable for %(type)s invoices (its type is '%(tax_type)s').")
                        % {
                            'row': row_number,
                            'tax': tax_name,
                            'type': type_tax_use,
                            'tax_type': tax_any.type_tax_use,
                        }
                    )
                raise UserError(
                    _("Row %(row)d: Tax '%(tax)s' not found. The tax name must match EXACTLY (case-insensitive) with an existing tax in Odoo. No new taxes will be created by this import.")
                    % {'row': row_number, 'tax': tax_name}
                )
            found_taxes |= tax
        return found_taxes

    @staticmethod
    def _parse_float(raw_value, field_label, row_number):
        cleaned = raw_value.strip().replace(' ', '')
        for sym in ('$', '€', '£', '₹', '¥', '₩'):
            cleaned = cleaned.replace(sym, '')

        if not cleaned:
            raise UserError(_("Row %d: '%s' is empty.") % (row_number, field_label))

        last_dot = cleaned.rfind('.')
        last_comma = cleaned.rfind(',')
        if last_comma > last_dot:
            cleaned = cleaned.replace('.', '').replace(',', '.')
        elif last_dot > last_comma and last_comma != -1:
            cleaned = cleaned.replace(',', '')

        try:
            return float(cleaned)
        except ValueError as exc:
            raise UserError(
                _("Row %(row)d: '%(label)s' value '%(val)s' is not a valid number.")
                % {'row': row_number, 'label': field_label, 'val': raw_value}
            ) from exc

    @staticmethod
    def _build_error_html(errors):
        """Build red HTML box listing all errors."""
        items = ''.join(
            '<li style="margin:2px 0;">%s</li>' % err.replace('\n', '<br/>')
            for err in errors
        )
        return (
            '<div style="padding:10px; background:#f8d7da; border:1px solid #f5c2c7; border-radius:4px; color:#842029;">'
            '<h4 style="margin-top:0;">❌ Test Failed</h4>'
            '<p><strong>%d error(s) found. Fix them and test again:</strong></p>'
            '<ul>%s</ul>'
            '</div>'
        ) % (len(errors), items)

    @staticmethod
    def _build_success_html(valid_rows):
        """Build green HTML box with preview of valid rows."""
        preview_items = ''.join(
            '<li style="margin:2px 0;">Row %d: Product ID %s, Qty %s, Price %s</li>'
            % (row_num, vals.get('product_id'), vals.get('quantity'), vals.get('price_unit'))
            for row_num, vals in valid_rows[:10]
        )
        more = ''
        if len(valid_rows) > 10:
            more = '<p><em>...and %d more row(s)</em></p>' % (len(valid_rows) - 10)
        return (
            '<div style="padding:10px; background:#d1e7dd; border:1px solid #badbcc; border-radius:4px; color:#0f5132;">'
            '<h4 style="margin-top:0;">✅ Test Passed</h4>'
            '<p><strong>%d line(s) ready to be imported.</strong></p>'
            '<p>You can now click <strong>Import Lines</strong>.</p>'
            '<details><summary>Preview (click to expand)</summary>'
            '<ul>%s</ul>%s</details>'
            '</div>'
        ) % (len(valid_rows), preview_items, more)