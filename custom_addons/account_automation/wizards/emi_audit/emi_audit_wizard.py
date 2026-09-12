from odoo import models, fields, api
from odoo.exceptions import UserError
import base64
import csv
import io


class EmiAuditWizard(models.TransientModel):
    _name = 'emi.audit.wizard'
    _description = 'EMI Audit Wizard'

    provider_id = fields.Many2one('emi.provider.config', string='EMI Provider', required=True)
    start_date = fields.Date(string='Start Date', required=True)
    end_date = fields.Date(string='End Date', required=True)
    emi_statement_file = fields.Binary(string='CSV File', required=True)
    file_name = fields.Char(string='File Name')

    col_identifier = fields.Char(related='provider_id.col_identifier', readonly=True)
    col_total_loan = fields.Char(related='provider_id.col_total_loan', readonly=True)
    col_advance = fields.Char(related='provider_id.col_advance', readonly=True)
    col_final_payout = fields.Char(related='provider_id.col_final_payout', readonly=True)
    col_fee_1 = fields.Char(related='provider_id.col_fee_1', readonly=True)
    col_fee_2 = fields.Char(related='provider_id.col_fee_2', readonly=True)
    col_settlement_date = fields.Char(related='provider_id.col_settlement_date', readonly=True)

    required_columns_help = fields.Text(
        string='Required CSV Columns',
        compute='_compute_required_columns_help',
        readonly=True
    )

    @api.depends('provider_id')
    def _compute_required_columns_help(self):
        for wiz in self:
            if not wiz.provider_id:
                wiz.required_columns_help = ''
                continue
            cfg = wiz.provider_id
            lines = [f"• Match Column: {cfg.col_identifier}"]
            if cfg.col_total_loan:
                lines.append(f"• Total Loan: {cfg.col_total_loan}")
            if cfg.col_advance:
                lines.append(f"• Advance: {cfg.col_advance}")
            if cfg.col_final_payout:
                lines.append(f"• Final Payout: {cfg.col_final_payout}")
            if cfg.col_fee_1:
                lines.append(f"• Fee 1: {cfg.col_fee_1}")
            if cfg.col_fee_2:
                lines.append(f"• Fee 2: {cfg.col_fee_2}")
            if cfg.col_settlement_date:
                lines.append(f"• Settlement Date: {cfg.col_settlement_date}")
            wiz.required_columns_help = '\n'.join(lines)

    def _safe_float(self, val):
        if not val:
            return 0.0
        try:
            return float(str(val).replace(',', '').strip() or '0')
        except Exception:
            return 0.0

    def _get_odoo_emi_by_invoice(self, cfg, csv_invoice_names):
        """
        Calculates exact reconciled EMI payment amounts per invoice.
        Matches both POS Payment Methods and Backend Payment Journals.
        """
        pos_method_ids = cfg.payment_method_ids.ids
        if not pos_method_ids:
            return {}

        # Get linked journals for backend payment matching
        journal_ids = cfg.payment_method_ids.mapped('journal_id').ids or [-1]

        csv_invoice_names = [n for n in (csv_invoice_names or []) if n]
        csv_names_param = csv_invoice_names or ['__none__']

        self.env.cr.execute("""
            SELECT
                am.name AS invoice_name,
                am.invoice_date AS inv_date,
                pc.id AS clinic_id,
                SUM(
                    CASE
                        WHEN aml_inv.id = apr.debit_move_id THEN apr.amount
                        ELSE -apr.amount
                    END
                ) AS total
            FROM account_move am
            JOIN account_move_line aml_inv ON aml_inv.move_id = am.id
            JOIN account_account acc ON acc.id = aml_inv.account_id
            JOIN account_partial_reconcile apr ON (
                apr.debit_move_id = aml_inv.id OR apr.credit_move_id = aml_inv.id
            )
            JOIN account_move_line aml_pay ON (
                (aml_pay.id = apr.credit_move_id AND aml_pay.id <> aml_inv.id)
                OR
                (aml_pay.id = apr.debit_move_id AND aml_pay.id <> aml_inv.id)
            )
            JOIN account_journal aj ON aj.id = aml_pay.journal_id
            LEFT JOIN pos_payment pp ON pp.account_move_id = aml_pay.move_id
            LEFT JOIN pos_payment_method ppm ON ppm.id = pp.payment_method_id
            LEFT JOIN pos_order po ON po.account_move = am.id
            LEFT JOIN pos_session ps ON ps.id = po.session_id
            LEFT JOIN pos_config pc ON pc.id = ps.config_id
            WHERE am.state = 'posted'
              AND am.move_type IN ('out_invoice', 'out_refund')
              AND acc.account_type = 'asset_receivable'
              AND (
                  ppm.id = ANY(%s)
                  OR aj.id = ANY(%s)
              )
              AND (
                  am.name = ANY(%s)
                  OR (am.invoice_date >= %s AND am.invoice_date <= %s)
              )
            GROUP BY am.name, am.invoice_date, pc.id
        """, (pos_method_ids, journal_ids, csv_names_param, self.start_date, self.end_date))

        result = {}
        for inv_name, inv_date, clinic_id, total in self.env.cr.fetchall():
            result[inv_name] = {
                'amount': total or 0.0,
                'clinic_id': clinic_id,
                'date': inv_date,
            }
        return result

    def _get_odoo_emi_by_clinic(self, cfg):
        """Clinic totals for non-invoice providers (SaveIn/Fibe/ShopSe)."""
        pos_method_ids = cfg.payment_method_ids.ids
        if not pos_method_ids:
            return {}

        journal_ids = cfg.payment_method_ids.mapped('journal_id').ids or [-1]

        self.env.cr.execute("""
            SELECT
                pc.id AS clinic_id,
                SUM(
                    CASE
                        WHEN aml_inv.id = apr.debit_move_id THEN apr.amount
                        ELSE -apr.amount
                    END
                ) AS total
            FROM account_move am
            JOIN account_move_line aml_inv ON aml_inv.move_id = am.id
            JOIN account_account acc ON acc.id = aml_inv.account_id
            JOIN account_partial_reconcile apr ON (
                apr.debit_move_id = aml_inv.id OR apr.credit_move_id = aml_inv.id
            )
            JOIN account_move_line aml_pay ON (
                (aml_pay.id = apr.credit_move_id AND aml_pay.id <> aml_inv.id)
                OR
                (aml_pay.id = apr.debit_move_id AND aml_pay.id <> aml_inv.id)
            )
            JOIN account_journal aj ON aj.id = aml_pay.journal_id
            LEFT JOIN pos_payment pp ON pp.account_move_id = aml_pay.move_id
            LEFT JOIN pos_payment_method ppm ON ppm.id = pp.payment_method_id
            LEFT JOIN pos_order po ON po.account_move = am.id
            LEFT JOIN pos_session ps ON ps.id = po.session_id
            LEFT JOIN pos_config pc ON pc.id = ps.config_id
            WHERE am.state = 'posted'
              AND am.move_type IN ('out_invoice', 'out_refund')
              AND acc.account_type = 'asset_receivable'
              AND (
                  ppm.id = ANY(%s)
                  OR aj.id = ANY(%s)
              )
              AND am.invoice_date >= %s
              AND am.invoice_date <= %s
            GROUP BY pc.id
        """, (pos_method_ids, journal_ids, self.start_date, self.end_date))

        return {cid: (total or 0.0) for cid, total in self.env.cr.fetchall()}

    def action_run_emi_audit(self):
        self.ensure_one()
        cfg = self.provider_id

        if not cfg.payment_method_ids:
            raise UserError("Please configure POS Payment Methods on the EMI Provider.")

        # 1. Parse CSV
        csv_text = base64.b64decode(self.emi_statement_file).decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise UserError("CSV file is empty or has no headers.")

        missing = []
        for label, col in [
            ('Match Column', cfg.col_identifier),
            ('Total Loan', cfg.col_total_loan),
            ('Final Payout', cfg.col_final_payout),
            ('Settlement Date', cfg.col_settlement_date),
        ]:
            if col and col not in reader.fieldnames:
                missing.append(f"{label}: '{col}'")
        if missing:
            raise UserError(
                "CSV is missing configured column(s):\n"
                + "\n".join(missing)
                + "\n\nFound headers:\n"
                + ", ".join(reader.fieldnames)
            )

        csv_data = list(reader)
        if not csv_data:
            raise UserError("CSV file is empty.")

        is_invoice_mode = (cfg.match_mode == 'invoice')

        # 2. Clinic mapping for clinic-name mode
        identifier_to_clinic_id = {}
        if not is_invoice_mode:
            for m in cfg.clinic_mapping_ids:
                identifier_to_clinic_id[m.csv_clinic_name.strip().lower()] = m.clinic_id.id

        # 3. Process CSV rows
        summary = {}
        csv_invoice_names = []

        for row in csv_data:
            ident_val = (row.get(cfg.col_identifier) or '').strip()
            if not ident_val:
                continue

            if is_invoice_mode:
                group_key = ident_val
                clinic_id = False
                csv_invoice_names.append(ident_val)
            else:
                clinic_id = identifier_to_clinic_id.get(ident_val.lower())
                group_key = clinic_id if clinic_id else f"__missing__{ident_val}"

            if group_key not in summary:
                summary[group_key] = {
                    'clinic_id': clinic_id,
                    'invoice_no': ident_val if is_invoice_mode else '',
                    'raw_name': ident_val if (not is_invoice_mode and not clinic_id) else '',
                    'settlement_date': (row.get(cfg.col_settlement_date) or '').strip(),
                    'total_loan': 0.0,
                    'advance': 0.0,
                    'expected_odoo_amount': 0.0,
                    'fees': 0.0,
                    'final_payout': 0.0,
                }

            s = summary[group_key]
            total_loan = self._safe_float(row.get(cfg.col_total_loan))
            advance = self._safe_float(row.get(cfg.col_advance)) if cfg.col_advance else 0.0
            fee_1 = self._safe_float(row.get(cfg.col_fee_1)) if cfg.col_fee_1 else 0.0
            fee_2 = self._safe_float(row.get(cfg.col_fee_2)) if cfg.col_fee_2 else 0.0
            final_payout = self._safe_float(row.get(cfg.col_final_payout))

            s['total_loan'] += total_loan
            s['advance'] += advance
            s['expected_odoo_amount'] += (total_loan - advance)
            s['fees'] += (fee_1 + fee_2)
            s['final_payout'] += final_payout

        # 4. Fetch Odoo EMI amounts
        if is_invoice_mode:
            odoo_by_invoice = self._get_odoo_emi_by_invoice(cfg, csv_invoice_names)
            odoo_by_clinic = {}
        else:
            odoo_by_invoice = {}
            odoo_by_clinic = self._get_odoo_emi_by_clinic(cfg)

        # 5. Add Odoo-only rows
        if is_invoice_mode:
            for inv_name, data in odoo_by_invoice.items():
                if inv_name not in summary:
                    summary[f'__odoo_only__{inv_name}'] = {
                        'clinic_id': data.get('clinic_id'),
                        'invoice_no': inv_name,
                        'raw_name': '',
                        'settlement_date': '',
                        'total_loan': 0.0,
                        'advance': 0.0,
                        'expected_odoo_amount': 0.0,
                        'fees': 0.0,
                        'final_payout': 0.0,
                    }
        else:
            csv_clinic_ids = {s['clinic_id'] for s in summary.values() if s['clinic_id']}
            for cid, amt in odoo_by_clinic.items():
                if cid not in csv_clinic_ids:
                    summary[f'__odoo_only__{cid}'] = {
                        'clinic_id': cid,
                        'invoice_no': '',
                        'raw_name': '',
                        'settlement_date': '',
                        'total_loan': 0.0,
                        'advance': 0.0,
                        'expected_odoo_amount': 0.0,
                        'fees': 0.0,
                        'final_payout': 0.0,
                    }

        # 6. Create audit
        audit = self.env['emi.audit'].create({
            'name': f"{cfg.name} ({self.start_date} to {self.end_date})",
            'provider_id': cfg.id,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'file_name': self.file_name,
        })

        lines_to_create = []
        for key, s in summary.items():
            if is_invoice_mode:
                inv_no = s['invoice_no']
                odoo_data = odoo_by_invoice.get(inv_no, {})
                odoo_amt = odoo_data.get('amount', 0.0)
                inv_date = odoo_data.get('date', False)
                cid = odoo_data.get('clinic_id') or s['clinic_id']
                cname = (
                    self.env['pos.config'].browse(cid).name
                    if cid else "— Unknown Clinic —"
                )
            else:
                cid = s['clinic_id']
                inv_no = ''
                inv_date = False
                odoo_amt = odoo_by_clinic.get(cid, 0.0) if cid else 0.0
                cname = (
                    self.env['pos.config'].browse(cid).name
                    if cid else f"— NOT MAPPED: {s['raw_name']} —"
                )

            lines_to_create.append({
                'audit_id': audit.id,
                'clinic_id': cid or False,
                'clinic_name': cname,
                'invoice_no': inv_no,
                'invoice_date': inv_date,
                'settlement_date': s['settlement_date'],
                'odoo_emi_sales': odoo_amt,
                'expected_odoo_amount': s['expected_odoo_amount'],
                'difference': odoo_amt - s['expected_odoo_amount'],
                'advance_paid': s['advance'],
                'fees_deducted': s['fees'],
                'final_payout': s['final_payout'],
            })

        if lines_to_create:
            self.env['emi.audit.line'].create(lines_to_create)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'emi.audit',
            'view_mode': 'form',
            'res_id': audit.id,
            'target': 'current',
        }