import csv
import io
import base64
from datetime import datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError


class ImportCSVWizard(models.TransientModel):
    _name = 'telemedicine.import.csv.wizard'
    _description = 'Import Telemedicine Invoices from CSV'

    file = fields.Binary(string='CSV File', required=True)
    file_name = fields.Char(string='File Name')
    state = fields.Selection([
        ('choose', 'Choose'),
        ('done', 'Done'),
    ], default='choose')
    summary = fields.Text(string='Import Summary', readonly=True)
    overwrite_existing = fields.Boolean(
        string='Overwrite Existing Invoices',
        default=False,
        help='If checked, existing invoices with the same number will be '
             'updated. Otherwise they are skipped.',
    )

    HEADER_MAP = {
        'invoice number': 'invoice_number',
        'invoice no': 'invoice_number',
        'invoice_no': 'invoice_number',
        'inv no': 'invoice_number',
        'inv number': 'invoice_number',
        'purchase order': 'purchase_order',
        'po': 'purchase_order',
        'po number': 'purchase_order',
        'customer id': 'customer_id',
        'customer_id': 'customer_id',
        'cust id': 'customer_id',
        'invoice date': 'invoice_date',
        'date': 'invoice_date',
        'inv date': 'invoice_date',
        'place of supply': 'place_of_supply',
        'pos': 'place_of_supply',
        'customer name': 'customer_name',
        'name': 'customer_name',
        'total': 'total',
        'amount': 'total',
        'grand total': 'total',
        'delivered': 'delivered',
        'delivery': 'delivered',
        'delivery status': 'delivered',
        'tax': 'tax_percent',
        'tax %': 'tax_percent',
        'tax percent': 'tax_percent',
        'gst': 'tax_percent',
        'gst %': 'tax_percent',
    }

    DATE_FORMATS = [
        '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y',
        '%d.%m.%Y', '%Y/%m/%d', '%d-%b-%Y', '%d-%B-%Y',
        '%d %b %Y', '%d %B %Y', '%Y-%m-%d %H:%M:%S',
    ]

    DELIVERED_YES = {'yes', 'y', 'true', '1', 'delivered', 'done', 'ok'}
    DELIVERED_NO = {
        'no', 'n', 'false', '0', 'not delivered', 'pending', 'undelivered',
    }

    def action_import(self):
        self.ensure_one()
        if not self.file:
            raise UserError(_('Please upload a CSV file.'))

        raw = base64.b64decode(self.file)
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise UserError(_('CSV file appears to be empty.'))

        col_map = {}
        for raw_col in reader.fieldnames:
            key = raw_col.strip().strip('"').strip("'").lower()
            if key in self.HEADER_MAP:
                col_map[raw_col] = self.HEADER_MAP[key]

        required = ['invoice_number', 'customer_id', 'invoice_date',
                     'customer_name', 'total']
        mapped_vals = set(col_map.values())
        missing = [r for r in required if r not in mapped_vals]
        if missing:
            raise UserError(_(
                'Missing required columns: %s\nDetected columns: %s'
            ) % (', '.join(missing), ', '.join(reader.fieldnames)))

        inv_obj = self.env['telemedicine.invoice']
        cust_obj = self.env['telemedicine.customer']
        config = self.env['telemedicine.import.config']._get_config()
        company = self.env.company

        created = 0
        updated = 0
        skipped = 0
        errors = []

        for row_num, row in enumerate(reader, start=2):
            try:
                mapped = {}
                for orig_col, field_key in col_map.items():
                    mapped[field_key] = (row.get(orig_col) or '').strip()

                inv_number = mapped.get('invoice_number', '')
                if not inv_number:
                    errors.append(f'Row {row_num}: Empty invoice number')
                    continue

                ext_id = mapped.get('customer_id', '')
                if not ext_id:
                    errors.append(
                        f'Row {row_num}: Missing Customer ID for '
                        f'invoice {inv_number}'
                    )
                    continue

                cust_name = mapped.get('customer_name', '')
                if not cust_name:
                    errors.append(
                        f'Row {row_num}: Missing Customer Name for '
                        f'invoice {inv_number}'
                    )
                    continue

                total_str = mapped.get('total', '')
                total = self._parse_float(total_str)
                if total is None:
                    errors.append(
                        f'Row {row_num}: Invalid total "{total_str}" '
                        f'for {inv_number}'
                    )
                    continue

                date_str = mapped.get('invoice_date', '')
                inv_date = self._parse_date(date_str)
                if not inv_date:
                    errors.append(
                        f'Row {row_num}: Invalid date "{date_str}" '
                        f'for {inv_number}'
                    )
                    continue

                pos = mapped.get('place_of_supply', '')
                delivered = mapped.get('delivered', '').strip()

                tax_str = mapped.get('tax_percent', '')
                tax_pct = self._parse_float(tax_str)
                if tax_pct is None:
                    tax_pct = config.default_tax_percent

                po = mapped.get('purchase_order', '')

                customer = cust_obj.search([
                    ('external_customer_id', '=', ext_id),
                    ('company_id', '=', company.id),
                ], limit=1)

                if not customer:
                    state_id = self._resolve_state(pos)
                    customer = cust_obj.create({
                        'name': cust_name,
                        'external_customer_id': ext_id,
                        'place_of_supply': pos,
                        'state_id': state_id,
                        'company_id': company.id,
                    })

                existing = inv_obj.search([
                    ('name', '=', inv_number),
                    ('company_id', '=', company.id),
                ], limit=1)

                vals = {
                    'name': inv_number,
                    'customer_id': customer.id,
                    'invoice_date': inv_date,
                    'purchase_order': po,
                    'place_of_supply': pos or customer.place_of_supply,
                    'delivered': delivered,
                    'amount_total': total,
                    'tax_percent': tax_pct,
                    'state': 'posted',
                    'company_id': company.id,
                }

                if existing:
                    if self.overwrite_existing:
                        existing.write(vals)
                        updated += 1
                    else:
                        skipped += 1
                else:
                    inv_obj.create(vals)
                    created += 1

            except Exception as e:
                errors.append(f'Row {row_num}: {str(e)}')

        error_text = ''
        if errors:
            shown = errors[:30]
            error_text = '\n\nErrors (showing first %d of %d):\n%s' % (
                len(shown), len(errors), '\n'.join(shown)
            )
            if len(errors) > 30:
                error_text += f'\n... and {len(errors) - 30} more.'

        self.summary = (
            f'Created: {created}\n'
            f'Updated: {updated}\n'
            f'Skipped (duplicates): {skipped}\n'
            f'Errors: {len(errors)}'
            f'{error_text}'
        )
        self.state = 'done'
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.model
    def _parse_float(self, val):
        if not val:
            return None
        val = val.replace(',', '').replace('\u20b9', '')
        val = val.replace('INR', '').replace('%', '').strip()
        try:
            return float(val)
        except ValueError:
            return None

    @api.model
    def _parse_date(self, val):
        if not val:
            return False
        val = val.strip()
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        return False

    @api.model
    def _resolve_state(self, pos):
        if not pos:
            return False
        pos = pos.strip()
        State = self.env['res.country.state']
        s = State.search([
            ('code', '=', pos.upper()),
            ('country_id.code', '=', 'IN'),
        ], limit=1)
        if s:
            return s.id
        s = State.search([
            ('code', '=', f'IN-{pos.upper()}'),
        ], limit=1)
        if s:
            return s.id
        s = State.search([
            ('name', 'ilike', pos),
            ('country_id.code', '=', 'IN'),
        ], limit=1)
        return s.id if s else False
