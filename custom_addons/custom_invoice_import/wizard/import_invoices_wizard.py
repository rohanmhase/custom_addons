import base64
import csv
import io
import re
from datetime import datetime
from odoo import models, fields, Command, _
from odoo.exceptions import UserError


class ImportInvoicesWizard(models.TransientModel):
    _name = 'import.invoices.wizard'
    _description = 'Import Invoices Wizard'

    file = fields.Binary(string='CSV File', required=True)
    file_name = fields.Char(string='File Name')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Validated'),
        ('error', 'Error'),
    ], default='draft')

    validation_summary = fields.Char(string='Validation Summary', readonly=True)

    existing_customer_ids = fields.One2many(
        'import.invoice.customer.existing', 'wizard_id', string='Existing Customers'
    )
    new_customer_ids = fields.One2many(
        'import.invoice.customer.new', 'wizard_id', string='New Customers'
    )

    HEADER_MAP = {
        'order_number': ['order number', 'salesorder number', 'so number', 'ref'],
        'customer_phone': ['receiver_phone', 'receiver phone', 'phone', 'mobile number', 'mobile'],
        'customer_name': ['customer name', 'name', 'receiver name'],
        'customer_address': ['customer address', 'address', 'street', 'billing address'],
        'customer_zip': ['pin code', 'pincode', 'zip', 'postal code'],
        'customer_state': ['place of supply', 'state', 'state name'],
        'product_name': ['item name', 'product name', 'product', 'item'],
        'qty': ['qty', 'quantity', 'item quantity'],
        'price': ['price', 'item price', 'unit price', 'rate'],
        'tax': ['tax %', 'tax', 'item tax %', 'item tax percent', 'tax percent'],
        'payment_method': ['payment method', 'payment mode', 'payment type'],
        # NEW OPTIONAL COLUMN
        'discount': [
            'entity discount percent', 'entity discount %',
            'discount %', 'discount%', 'discount percent',
            'item discount %', 'disc %', 'disc%'
        ],
        'utr': ['utr', 'utr number', 'transaction id', 'reference number'],
        'delivery_date': ['delivery date', 'delivered date', 'delivered on'],
    }

    def _parse_date(self, raw_value):
        if not raw_value: return False
        value = str(raw_value).strip()
        for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y', '%d.%m.%Y', '%Y/%m/%d'):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
        return False

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------
    def _clean_phone(self, phone):
        if not phone:
            return ''
        digits = re.sub(r'\D', '', str(phone))
        if len(digits) >= 10:
            return digits[-10:]
        return digits

    def _names_loosely_match(self, name_a, name_b):
        a = re.sub(r'\s+', '', (name_a or '').lower())
        b = re.sub(r'\s+', '', (name_b or '').lower())
        if not a or not b:
            return True
        return a in b or b in a

    def _find_partner_by_phone(self, phone_clean):
        if not phone_clean:
            return self.env['res.partner']
        Partner = self.env['res.partner']
        candidates = Partner.search([
            '|',
            ('phone', 'ilike', phone_clean),
            ('mobile', 'ilike', phone_clean),
        ], limit=20)
        for p in candidates:
            p_phone = self._clean_phone(p.phone)
            p_mobile = self._clean_phone(p.mobile)
            if phone_clean in (p_phone, p_mobile):
                return p
        return Partner

    def _find_gst_tax(self, tax_pct, place_of_supply, company_state):
        Tax = self.env['account.tax']
        all_sale_taxes = Tax.search([('type_tax_use', '=', 'sale')])

        if tax_pct == 0.0:
            return Tax

        pos_clean = (place_of_supply or '').strip().upper()
        comp_code = (company_state.code or 'MH').upper() if company_state else 'MH'
        comp_name = (company_state.name or 'MAHARASHTRA').upper() if company_state else 'MAHARASHTRA'

        is_intra = pos_clean in [comp_code, comp_name, 'MH', 'MAHARASHTRA', '27']
        pct_str = f"{int(tax_pct) if tax_pct.is_integer() else tax_pct}%"

        matched_tax = Tax

        for tax in all_sale_taxes:
            name_upper = tax.name.upper()
            pct_matches = (abs(tax.amount - tax_pct) < 0.01) or (pct_str in name_upper) or (f"{tax_pct}%" in name_upper)
            if not pct_matches:
                continue

            if is_intra:
                if 'IGST' not in name_upper and (
                        'GST' in name_upper or 'CGST' in name_upper or 'SGST' in name_upper or abs(
                    tax.amount - tax_pct) < 0.01):
                    matched_tax = tax
                    break
            else:
                if 'IGST' in name_upper or 'INTER' in name_upper:
                    matched_tax = tax
                    break

        if not matched_tax:
            for tax in all_sale_taxes:
                name_upper = tax.name.upper()
                if abs(tax.amount - tax_pct) < 0.01 or pct_str in name_upper:
                    if is_intra and 'IGST' not in name_upper:
                        matched_tax = tax
                        break
                    elif not is_intra and 'IGST' in name_upper:
                        matched_tax = tax
                        break

        if not matched_tax:
            matched_tax = all_sale_taxes.filtered(lambda t: abs(t.amount - tax_pct) < 0.01 or pct_str in t.name)
            if matched_tax:
                matched_tax = matched_tax[0]

        return matched_tax

    # ------------------------------------------------------------------
    # CSV Processing
    # ------------------------------------------------------------------
    def _decode_csv(self):
        if not self.file:
            raise UserError(_("Please upload a CSV file."))
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

        required = [
            'order_number', 'customer_phone', 'customer_name',
            'product_name', 'qty', 'price', 'payment_method'
        ]
        missing = [k for k in required if k not in col_index]
        if missing:
            raise UserError(_(
                "Missing required column(s): %s\nFound headers: %s"
            ) % (', '.join(missing), ', '.join([h for h in raw_headers if h])))

        data_rows = []
        for line_no, row in enumerate(rows[1:], start=2):
            if not row or not any(str(c or '').strip() for c in row):
                continue

            def get(key):
                if key not in col_index:
                    return ''
                idx = col_index[key]
                return str(row[idx]).strip() if idx < len(row) and row[idx] is not None else ''

            data_rows.append({
                'line_no': line_no,
                'order_number': get('order_number'),
                'customer_phone': get('customer_phone'),
                'customer_name': get('customer_name'),
                'customer_address': get('customer_address'),
                'customer_zip': get('customer_zip'),
                'customer_state': get('customer_state'),
                'product_name': get('product_name'),
                'qty': get('qty'),
                'price': get('price'),
                'tax': get('tax'),
                'discount': get('discount'),
                'payment_method': get('payment_method'),
                'utr': get('utr'),
                'delivery_date': self._parse_date(get('delivery_date')),
            })
        return data_rows

    def _validate_rows(self, data_rows):
        errors = []
        grouped_orders = {}

        existing_map = {}
        new_map = {}

        product_cache = {}
        tax_cache = {}

        Product = self.env['product.product']
        State = self.env['res.country.state']
        CountryIN = self.env['res.country'].search([('code', '=', 'IN')], limit=1)
        company_state = self.env.company.state_id or State.search(
            [('code', '=', 'MH'), ('country_id', '=', CountryIN.id)], limit=1
        )

        for row in data_rows:
            line = row['line_no']
            line_errors = []

            order_no = (row['order_number'] or '').strip()
            if not order_no:
                line_errors.append("Order Number is empty")

            csv_name = (row['customer_name'] or '').strip()
            csv_phone_raw = (row['customer_phone'] or '').strip()
            phone_clean = self._clean_phone(csv_phone_raw)

            if not phone_clean or len(phone_clean) < 10:
                line_errors.append("Receiver_phone missing/invalid (need at least 10 digits)")
            if not csv_name:
                line_errors.append("Customer Name is required")

            if phone_clean and csv_name and phone_clean not in existing_map and phone_clean not in new_map:
                partner = self._find_partner_by_phone(phone_clean)
                if partner:
                    db_phone_show = partner.mobile or partner.phone or ''
                    db_address = ", ".join([x for x in [
                        partner.street or '', partner.street2 or '',
                        partner.city or '', partner.zip or '',
                        partner.state_id.name if partner.state_id else '',
                    ] if x])
                    csv_address = ", ".join([x for x in [
                        row.get('customer_address') or '',
                        row.get('customer_zip') or '',
                        row.get('customer_state') or '',
                    ] if x])

                    existing_map[phone_clean] = {
                        'partner_id': partner.id,
                        'db_name': partner.name or '',
                        'db_phone': db_phone_show,
                        'db_address': db_address,
                        'csv_name': csv_name,
                        'csv_phone': csv_phone_raw,
                        'csv_address': csv_address,
                    }
                else:
                    state_id = False
                    pos_val = row['customer_state']
                    if pos_val and CountryIN:
                        st = State.search([
                            ('country_id', '=', CountryIN.id),
                            '|', '|',
                            ('code', '=ilike', pos_val),
                            ('code', '=ilike', f"IN-{pos_val}"),  # Handles 'HR' -> 'IN-HR', 'MH' -> 'IN-MH'
                            ('name', '=ilike', pos_val),
                        ], limit=1)
                        state_id = st.id if st else False

                    new_map[phone_clean] = {
                        'name': csv_name,
                        'phone': csv_phone_raw,
                        'mobile': csv_phone_raw,
                        'street': row['customer_address'],
                        'zip': row['customer_zip'],
                        'state_id': state_id,
                        'country_id': CountryIN.id if CountryIN else False,
                    }

            # Product by name
            p_name = (row['product_name'] or '').strip()
            if not p_name:
                line_errors.append("Item Name is empty")
            else:
                if p_name not in product_cache:
                    prod = Product.search([('name', '=ilike', p_name)], limit=1)
                    if not prod:
                        line_errors.append("Product '%s' not found" % p_name)
                    else:
                        product_cache[p_name] = prod.id

            # Numerics
            qty = price = tax_pct = None
            try:
                qty = float(row['qty'])
            except Exception:
                line_errors.append("Invalid Qty '%s'" % row['qty'])
            try:
                price = float(row['price'])
            except Exception:
                line_errors.append("Invalid Price '%s'" % row['price'])
            try:
                tax_pct = float(row['tax'] or 0.0)
            except Exception:
                line_errors.append("Invalid Tax %% '%s'" % row['tax'])

            # NEW - Entity Discount Percent (optional column)
            disc_pct = 0.0
            disc_raw = (row.get('discount') or '').strip()
            if disc_raw:
                try:
                    disc_pct = round(float(disc_raw), 2)  # Odoo accepts 2-decimals
                    if disc_pct < 0.0 or disc_pct > 100.0:
                        line_errors.append("Discount %% must be between 0 and 100 (got %s)" % disc_raw)
                        disc_pct = 0.0
                except Exception:
                    line_errors.append("Invalid Discount %% '%s'" % disc_raw)
                    disc_pct = 0.0

            # Tax matcher
            tax_ids = []
            if tax_pct is not None and tax_pct != 0.0:
                pos = row['customer_state'] or 'MH'
                cache_key = (tax_pct, pos.upper())
                if cache_key not in tax_cache:
                    tax_cache[cache_key] = self._find_gst_tax(tax_pct, pos, company_state)

                tax_rec = tax_cache[cache_key]
                if not tax_rec:
                    line_errors.append(
                        f"Sales tax {tax_pct}% for '{pos}' not configured in Accounting -> Taxes"
                    )
                elif tax_rec:
                    tax_ids = tax_rec.ids

            # Payment method
            pm_raw = (row['payment_method'] or '').strip().lower()
            if pm_raw in ('prepaid', 'pre-paid', 'pre paid'):
                payment_method = 'prepaid'
            elif pm_raw in ('pay on delivery', 'pod', 'cod', 'cash on delivery'):
                payment_method = 'pod'
            else:
                payment_method = None
                line_errors.append("Unknown Payment Method '%s'" % row['payment_method'])

            if line_errors:
                errors.append("Line %s: %s" % (line, '; '.join(line_errors)))
                continue

            if order_no not in grouped_orders:
                grouped_orders[order_no] = {
                    'customer_phone': phone_clean,
                    'payment_method': payment_method,
                    'utr': row.get('utr'),
                    'delivery_date': row.get('delivery_date'),
                    'lines': [],
                }
            else:
                if grouped_orders[order_no]['customer_phone'] != phone_clean:
                    errors.append("Line %s: different customer phone inside same Order %s" % (line, order_no))
                    continue
                if grouped_orders[order_no]['payment_method'] != payment_method:
                    errors.append("Line %s: different payment method inside same Order %s" % (line, order_no))
                    continue

            grouped_orders[order_no]['lines'].append({
                'product_id': product_cache[p_name],
                'product_name': p_name,
                'quantity': qty,
                'price_unit': price,
                'discount': disc_pct,  # NEW native Odoo discount %
                'tax_ids': tax_ids,
            })

        return errors, grouped_orders, new_map, existing_map

    # ------------------------------------------------------------------
    # Wizard Button Actions
    # ------------------------------------------------------------------
    def action_validate(self):
        self.ensure_one()
        self.existing_customer_ids.unlink()
        self.new_customer_ids.unlink()

        try:
            data_rows = self._decode_csv()
            errors, grouped, new_map, existing_map = self._validate_rows(data_rows)
        except UserError as e:
            self.write({
                'state': 'error',
                'validation_summary': f"Error: {e.args[0]}",
            })
            return self._reload()

        if errors:
            raise UserError(_("Validation Failed:\n") + "\n".join(errors[:20]))

        existing_cmds = []
        for phone, info in existing_map.items():
            existing_cmds.append((0, 0, {
                'csv_name': info['csv_name'],
                'csv_phone': info['csv_phone'],
                'csv_address': info['csv_address'],
                'db_name': info['db_name'],
                'db_phone': info['db_phone'],
                'db_address': info['db_address'],
                'normalized_phone': phone,
                'has_warning': not self._names_loosely_match(info['csv_name'], info['db_name']),
            }))

        new_cmds = []
        for phone, info in new_map.items():
            new_cmds.append((0, 0, {
                'name': info['name'],
                'phone': info['phone'],
                'street': info['street'],
                'zip': info['zip'],
                'normalized_phone': phone,
            }))

        summary = _(
            "SUCCESS: %s Orders | %s Lines | %s Existing Customers | %s New Customers"
        ) % (len(grouped), len(data_rows), len(existing_map), len(new_map))

        self.write({
            'state': 'done',
            'validation_summary': summary,
            'existing_customer_ids': existing_cmds,
            'new_customer_ids': new_cmds,
        })
        return self._reload()

    def action_import(self):
        self.ensure_one()
        if self.state != 'done':
            raise UserError(_("Please run Test/Validate successfully before importing."))

        config = self.env['online.import.config'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1) or self.env['online.import.config'].search([], limit=1)

        if not config or not config.prepaid_journal_id:
            raise UserError(_("Please set the Prepaid Bank Journal in Invoicing -> Configuration -> Online Imports."))

        data_rows = self._decode_csv()
        errors, grouped, new_map, existing_map = self._validate_rows(data_rows)
        if errors:
            raise UserError(_("File is invalid. Please Validate again.\n%s") % "\n".join(errors[:10]))

        Partner = self.env['res.partner']
        phone_to_partner_id = {}

        for phone, info in existing_map.items():
            phone_to_partner_id[phone] = info['partner_id']

        for phone, vals in new_map.items():
            partner = self._find_partner_by_phone(phone)
            if partner:
                phone_to_partner_id[phone] = partner.id
            else:
                partner = Partner.create(vals)
                phone_to_partner_id[phone] = partner.id

        journal = self.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', self.env.company.id)
        ], limit=1) or self.env['account.journal'].search([('type', '=', 'sale')], limit=1)

        if not journal:
            raise UserError(_("No Sales Journal found."))

        if not config.merged_product_id:
            raise UserError(_("Set Medicine Merged Product in Online Imports Configuration first."))

        split_date = config.split_start_date or fields.Date.from_string('2026-11-01')
        today_date = fields.Date.context_today(self)

        move_vals_list = []
        prepaid_refs = []
        teleconsult_map = {}

        for order_no, data in grouped.items():
            partner_id = phone_to_partner_id.get(data['customer_phone'])
            if not partner_id:
                raise UserError(_("No customer resolved for order %s") % order_no)

            inv_date = today_date

            # Calculate totals (untaxed + tax-inclusive) and collect medicine list
            total_untaxed = 0.0
            total_incl = 0.0
            tax_ids = []
            tax_pct = 0.0
            tele_lines = []

            Tax = self.env['account.tax']
            for line in data['lines']:
                sub_base = line['quantity'] * line['price_unit']
                if line.get('discount'):
                    sub_base *= (1.0 - line['discount'] / 100.0)

                line_tax_pct = 0.0
                if line['tax_ids']:
                    tax_ids = line['tax_ids']
                    line_taxes = Tax.browse(line['tax_ids'])
                    line_tax_pct = sum(t.amount for t in line_taxes if t.amount_type == 'percent')
                    tax_pct = line_tax_pct

                sub_incl = sub_base * (1.0 + line_tax_pct / 100.0)
                total_untaxed += sub_base
                total_incl += sub_incl

                tele_lines.append({
                    'product_name': line['product_name'],
                    'quantity': line['quantity'],
                })

            teleconsult_map[order_no] = tele_lines
            lines_cmd = []

            # DECISION: 1 Line (before split date) vs 2 Lines (on/after split date)
            if inv_date >= split_date and config.consultation_product_id:
                consult_product = config.consultation_product_id
                consult_std = config.consultation_amount or 600.0
                min_med_incl = config.min_medicine_amount or 50.0

                if total_incl >= (consult_std + min_med_incl):
                    # Normal case
                    consult_price = consult_std
                    consult_disc = 0.0
                    med_target_incl = total_incl - consult_std
                else:
                    # Edge case: total < 600 + min medicine
                    med_target_incl = min_med_incl
                    consult_target_net = max(total_incl - min_med_incl, 0.0)
                    consult_price = consult_std
                    consult_disc = (
                        ((consult_std - consult_target_net) / consult_std) * 100.0
                        if consult_std else 0.0
                    )

                med_base_price = (
                    med_target_incl / (1.0 + tax_pct / 100.0)
                    if (1.0 + tax_pct / 100.0) else med_target_incl
                )

                # Line 1: Doctor Consultation (0% tax)
                lines_cmd.append(Command.create({
                    'product_id': consult_product.id,
                    'quantity': 1.0,
                    'price_unit': round(consult_price, 2),
                    'discount': round(consult_disc, 2),
                    'tax_ids': [],
                }))
                # Line 2: Medicine (remaining, with tax)
                # Line 2: Medicine (remaining, with tax)
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
                # Pre-split date: single merged medicine line
                lines_cmd.append(Command.create({
                    'product_id': config.merged_product_id.id,
                    'quantity': 1.0,
                    'price_unit': round(total_untaxed, 2),
                    'discount': 0.0,
                    'tax_ids': [Command.set(tax_ids)] if tax_ids else [],
                }))

            move_vals_list.append({
                'move_type': 'out_invoice',
                'journal_id': journal.id,
                'partner_id': partner_id,
                'invoice_date': inv_date,
                'invoice_origin': order_no,
                'ref': order_no,
                'invoice_line_ids': lines_cmd,
            })
            if data['payment_method'] == 'prepaid':
                prepaid_refs.append(order_no)

        moves = self.env['account.move'].create(move_vals_list)
        moves.action_post()

        TeleLine = self.env['teleconsult.invoice.line']
        prepaid_moves = self.env['account.move']
        for move in moves:
            data = grouped.get(move.ref)
            if not data:
                continue

            self.env['online.order'].create({
                'name': move.ref,
                'move_id': move.id,
                'payment_method': data['payment_method'],
                'utr': data.get('utr'),
                'delivery_date': data.get('delivery_date'),
            })

            for tl in teleconsult_map.get(move.ref, []):
                TeleLine.create({
                    'move_id': move.id,
                    'product_name': tl['product_name'],
                    'quantity': tl['quantity'],
                })

            if data['payment_method'] == 'prepaid':
                prepaid_moves |= move

        if prepaid_moves:
            for move in prepaid_moves:
                pay = self.env['account.payment.register'].with_company(move.company_id).with_context(
                    active_model='account.move',
                    active_ids=move.ids,
                    default_company_id=move.company_id.id,
                ).create({
                    'journal_id': config.prepaid_journal_id.id,
                    'payment_date': move.invoice_date or fields.Date.context_today(self),
                })
                pay.action_create_payments()

        return {
            'name': _('Imported Invoices'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'tree,form',
            'domain': [('id', 'in', moves.ids)],
            'target': 'current',
        }

    def action_reset(self):
        self.write({
            'state': 'draft',
            'validation_summary': False,
            'file': False,
            'file_name': False,
        })
        self.existing_customer_ids.unlink()
        self.new_customer_ids.unlink()
        return self._reload()

    def _reload(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ImportCustomerExisting(models.TransientModel):
    _name = 'import.invoice.customer.existing'
    _description = 'Existing Customer Match Preview'

    wizard_id = fields.Many2one('import.invoices.wizard', ondelete='cascade')
    csv_name = fields.Char(string='CSV Name')
    csv_phone = fields.Char(string='CSV Phone')
    csv_address = fields.Char(string='CSV Address')
    db_name = fields.Char(string='DB Name')
    db_phone = fields.Char(string='DB Phone')
    db_address = fields.Char(string='DB Address')
    normalized_phone = fields.Char(string='Matched Phone')
    has_warning = fields.Boolean(string='Name Mismatch')


class ImportCustomerNew(models.TransientModel):
    _name = 'import.invoice.customer.new'
    _description = 'New Customer Preview'

    wizard_id = fields.Many2one('import.invoices.wizard', ondelete='cascade')
    name = fields.Char(string='Name')
    phone = fields.Char(string='Phone')
    street = fields.Char(string='Address')
    zip = fields.Char(string='PIN Code')
    normalized_phone = fields.Char(string='Normalized Phone')
