from odoo import models, fields
import base64
import csv
import io


class BankSalesAuditWizard(models.TransientModel):
    _name = 'bank.sales.audit.wizard'
    _description = 'Bank vs HUB Audit Wizard'

    bank_config_id = fields.Many2one(
        'bank.statement.parser.config',
        string='Bank',
        required=True
    )
    start_date = fields.Date(string='Start Date', required=True)
    end_date = fields.Date(string='End Date', required=True)
    bank_statement_file = fields.Binary(string='CSV File', required=True)
    file_name = fields.Char(string='File Name')

    def action_run_bank_vs_hub_audit(self):
        self.ensure_one()
        cfg = self.bank_config_id

        # ── 1. Build label maps ──────────────────────────────────────────────
        #
        #  raw_to_label      : 'UPI_LITE' → 'ICICI UPI'   (bank CSV side)
        #  method_id_to_label: 5          → 'ICICI UPI'   (kept for reference)
        #  method_name_to_label: 'ICICI UPI' → 'ICICI UPI' (reconciliation side —
        #       the reconciliation query returns pos_payment_method.name as TEXT,
        #       not an id, so we match on name, not id)
        #
        raw_to_label = {}
        method_id_to_label = {}
        allowed_method_ids_set = set()

        for m in cfg.mapping_ids:
            raw_to_label[m.raw_value] = m.label
            for mid in m.system_mode_ids.ids:
                method_id_to_label[mid] = m.label
                allowed_method_ids_set.add(mid)

        allowed_method_ids = list(allowed_method_ids_set)

        if not allowed_method_ids:
            return self._no_data_notification()

        # Resolve pos.payment.method names (en_US) → label, for matching
        # against the reconciliation query's "method_name" text column.
        self.env.cr.execute("""
            SELECT id, name->>'en_US'
            FROM pos_payment_method
            WHERE id = ANY(%s)
        """, (allowed_method_ids,))
        method_id_name = dict(self.env.cr.fetchall())

        method_name_to_label = {}
        for mid, label in method_id_to_label.items():
            name = method_id_name.get(mid)
            if name:
                method_name_to_label[name] = label

        # ── 2. TID ↔ Clinic mapping ───────────────────────────────────────────
        tid_mappings = self.env['tid.clinic.method.mapping'].search([
            ('bank_config_id', '=', cfg.id)
        ])
        clinic_names_by_tid = {}     # tid -> [clinic names]  (for display)
        clinic_to_tid = {}           # clinic_id -> tid_number (1 clinic = 1 TID)
        for m in tid_mappings:
            clinic_names_by_tid.setdefault(m.tid_number, []).append(m.clinic_id.name)
            clinic_to_tid[m.clinic_id.id] = m.tid_number

        clinic_display_map = {
            tid: ", ".join(names)
            for tid, names in clinic_names_by_tid.items()
        }

        # ── 3. Parse Bank CSV → bank_totals[(tid, label)] ────────────────────
        bank_totals = {}
        csv_text = base64.b64decode(self.bank_statement_file).decode('utf-8')
        reader = csv.DictReader(io.StringIO(csv_text))

        for row in reader:
            tid = row.get(cfg.col_tid, '').strip()
            raw_mode = row.get(cfg.col_mode, '').strip()

            if raw_mode not in raw_to_label:
                continue

            try:
                amount = float(row.get(cfg.col_amount, '0').replace(',', '').strip())
            except ValueError:
                continue

            key = (tid, raw_to_label[raw_mode])
            bank_totals[key] = bank_totals.get(key, 0.0) + amount

        # ── 4. HUB totals via RECONCILIATION (not pos_payment) ───────────────
        #
        #  Why: pos_payment.amount is a static snapshot at order creation time
        #  and never reflects refunds reconciled later against a different
        #  invoice_date. account_partial_reconcile + invoice_date gives the
        #  TRUE amount settled for that date, including late refunds.
        #
        #  method_sum sign is already correct:
        #    normal invoice → receivable is debit  → +apr.amount
        #    refund         → receivable is credit → -apr.amount
        #
        self.env.cr.execute("""
            SELECT
                pc.id AS clinic_id,
                COALESCE(
                    ppm.name->>'en_US',
                    CASE WHEN aj.name->>'en_US' IN ('Manual', 'Point of Sale')
                         THEN apml.name
                         ELSE aj.name->>'en_US'
                    END,
                    apml.name,
                    aj.name::text
                ) AS method_name,
                SUM(
                    CASE
                        WHEN aml_inv.id = apr.debit_move_id THEN apr.amount
                        ELSE -apr.amount
                    END
                ) AS total
            FROM account_move am
            JOIN account_move_line aml_inv ON aml_inv.move_id = am.id
            JOIN account_account acc ON acc.id = aml_inv.account_id
            JOIN account_partial_reconcile apr
                 ON (apr.debit_move_id = aml_inv.id OR apr.credit_move_id = aml_inv.id)
            JOIN account_move_line aml_pay ON (
                (aml_pay.id = apr.credit_move_id AND aml_pay.id <> aml_inv.id) OR
                (aml_pay.id = apr.debit_move_id AND aml_pay.id <> aml_inv.id)
            )
            JOIN account_journal aj ON aj.id = aml_pay.journal_id
            LEFT JOIN account_payment ap ON ap.id = aml_pay.payment_id
            LEFT JOIN account_payment_method_line apml ON apml.id = ap.payment_method_line_id
            LEFT JOIN pos_payment pp ON pp.account_move_id = aml_pay.move_id
            LEFT JOIN pos_payment_method ppm ON ppm.id = pp.payment_method_id
            LEFT JOIN pos_order po ON po.account_move = am.id
            LEFT JOIN pos_session ps ON ps.id = po.session_id
            LEFT JOIN pos_config pc ON pc.id = ps.config_id
            WHERE am.state = 'posted'
              AND am.move_type IN ('out_invoice', 'out_refund')
              AND acc.account_type = 'asset_receivable'
              AND pc.id IS NOT NULL
              AND am.invoice_date >= %s
              AND am.invoice_date <= %s
            GROUP BY pc.id, method_name
        """, (self.start_date, self.end_date))

        # ── 5. Fold into (tid, label) / (clinic_id, label) for unmapped ─────
        hub_totals = {}          # (tid, label) -> amount
        unmapped_hub = {}        # (clinic_id, label) -> amount

        for clinic_id, method_name, amount in self.env.cr.fetchall():
            label = method_name_to_label.get(method_name)
            if not label:
                continue   # this payment method isn't part of this bank's tracked set

            tid = clinic_to_tid.get(clinic_id)
            if tid:
                key = (tid, label)
                hub_totals[key] = hub_totals.get(key, 0.0) + amount
            else:
                key = (clinic_id, label)
                unmapped_hub[key] = unmapped_hub.get(key, 0.0) + amount

        # clinic_id → name, for unmapped display
        unmapped_clinic_name = {}
        if unmapped_hub:
            ids = list({cid for cid, _ in unmapped_hub.keys()})
            unmapped_clinic_name = {
                c.id: c.name for c in self.env['pos.config'].browse(ids)
            }

        # ── 6. Union of all (tid, label) pairs ────────────────────────────────
        all_keys = set(bank_totals.keys()) | set(hub_totals.keys())

        if not all_keys and not unmapped_hub:
            return self._no_data_notification()

        # ── 7. Audit header ───────────────────────────────────────────────────
        audit = self.env['bank.sales.audit'].create({
            'name': f"{cfg.bank_name} ({self.start_date} \u2013 {self.end_date})",
            'bank_config_id': cfg.id,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'file_name': self.file_name,
        })

        # ── 8. Result lines — mapped TIDs ────────────────────────────────────
        result_lines = []
        for tid, label in all_keys:
            h_amt = hub_totals.get((tid, label), 0.0)
            b_amt = bank_totals.get((tid, label), 0.0)
            result_lines.append({
                'audit_id': audit.id,
                'tid_number': tid,
                'clinics_display': clinic_display_map.get(tid, 'TID Not Mapped'),
                'system_mode': label,
                'hub_amount': h_amt,
                'bank_amount': b_amt,
                'difference': h_amt - b_amt,
            })

        # ── 9. Result lines — unmapped clinics ───────────────────────────────
        for (clinic_id, label), h_amt in unmapped_hub.items():
            result_lines.append({
                'audit_id': audit.id,
                'tid_number': '— NO TID MAPPED —',
                'clinics_display': unmapped_clinic_name.get(clinic_id, ''),
                'system_mode': label,
                'hub_amount': h_amt,
                'bank_amount': 0.0,
                'difference': h_amt,
            })

        self.env['bank.sales.audit.line'].create(result_lines)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bank.sales.audit',
            'view_mode': 'form',
            'res_id': audit.id,
            'target': 'current',
        }

    def _no_data_notification(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'No Data',
                'message': 'No transactions found for the selected bank and date range.',
                'type': 'warning',
            },
        }