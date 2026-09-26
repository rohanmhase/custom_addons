from odoo import models, fields
import base64
import csv
import io
import datetime


class CashDepositAuditWizard(models.TransientModel):
    _name = 'cash.deposit.audit.wizard'
    _description = 'Cash Deposit Audit Wizard'

    start_date = fields.Date(string='From Date', required=True)
    end_date = fields.Date(string='To Date', required=True)
    bank_statement_file = fields.Binary(string='Bank Statement CSV', required=True)
    file_name = fields.Char(string='File Name')

    def action_run_audit(self):
        self.ensure_one()

        # ── 1. Clinic name mapping (.sudo() on model level) ──
        mappings = self.env['cash.deposit.clinic.mapping'].sudo().search([])
        map_by_raw_name = {
            m.raw_clinic_name.strip().lower(): m.clinic_id
            for m in mappings if m.raw_clinic_name
        }
        odoo_clinics = self.env['clinic.clinic'].sudo().search([])
        map_by_direct_name = {
            (c.name or '').strip().lower(): c
            for c in odoo_clinics if c.name
        }

        # ── 2. Parse CSV ──
        try:
            csv_text = base64.b64decode(self.bank_statement_file).decode('utf-8-sig')
        except Exception:
            return self._notify("Invalid File", "Could not decode CSV. Please use UTF-8.", 'danger')

        reader = csv.DictReader(io.StringIO(csv_text))
        headers = reader.fieldnames or []
        required_cols = ['Date', 'DEPOSIT AMT', 'Clinic']
        missing = [c for c in required_cols if c not in headers]
        if missing:
            return self._notify(
                "Missing Columns",
                f"CSV must contain: {', '.join(required_cols)}. Missing: {', '.join(missing)}",
                'danger'
            )

        # bank_data key schemes:
        #   (date, clinic_id)          → mapped clinic
        #   (date, '__NO_CLINIC__')    → Clinic column blank
        #   (date, raw_name_string)    → Clinic text present but not mapped
        bank_data = {}
        NO_CLINIC_KEY = '__NO_CLINIC__'

        for row in reader:
            raw_date = (row.get('Date') or '').strip()
            raw_dep = (row.get('DEPOSIT AMT') or '').replace(',', '').strip()
            raw_clinic = (row.get('Clinic') or '').strip()

            # Date + amount are mandatory; Clinic may be blank
            if not raw_date or not raw_dep:
                continue

            try:
                dep_amt = float(raw_dep)
                if dep_amt <= 0:
                    continue
            except ValueError:
                continue

            parsed_date = None
            for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%m/%d/%Y'):
                try:
                    parsed_date = datetime.datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            if not parsed_date or not (self.start_date <= parsed_date <= self.end_date):
                continue

            # ── Classify the clinic cell ──
            if not raw_clinic:
                key = (parsed_date, NO_CLINIC_KEY)
            else:
                mapped = (
                    map_by_raw_name.get(raw_clinic.lower())
                    or map_by_direct_name.get(raw_clinic.lower())
                )
                if mapped:
                    key = (parsed_date, mapped.id)          # int clinic_id
                else:
                    key = (parsed_date, raw_clinic)         # unmapped raw string

            bank_data[key] = bank_data.get(key, 0.0) + dep_amt

        # ── 3. Pull POS Cash In / Cash Out with backdating logic ──
        # Raw SQL already bypasses ORM record rules, so all clinics are returned.
        # target_date rule (Req #7):
        #  - if create_date is within session start/stop → use create_date::date
        #  - else fallback to session.start_at::date
        self.env.cr.execute("""
            SELECT
                absl.id,
                absl.amount,
                absl.payment_ref,
                absl.create_date AT TIME ZONE 'UTC' AS create_dt,
                ps.start_at AT TIME ZONE 'UTC' AS session_start,
                ps.stop_at  AT TIME ZONE 'UTC' AS session_stop,
                cc.id      AS clinic_id,
                cc.name    AS clinic_name,
                rp.name    AS responsible_person
            FROM account_bank_statement_line absl
            JOIN pos_session ps ON ps.id = absl.pos_session_id
            JOIN pos_config  pc ON pc.id = ps.config_id
            JOIN clinic_clinic cc ON cc.id = pc.clinic_id
            LEFT JOIN res_users   ru ON ru.id = absl.create_uid
            LEFT JOIN res_partner rp ON rp.id = ru.partner_id
            WHERE (absl.payment_ref ILIKE '%%-in-%%' OR absl.payment_ref ILIKE '%%-out-%%')
              AND (
                    absl.create_date::date BETWEEN %s AND %s
                 OR ps.start_at::date      BETWEEN %s AND %s
              )
        """, (self.start_date, self.end_date, self.start_date, self.end_date))

        rows = self.env.cr.dictfetchall()

        # pos_grouped key: (target_date, clinic_id) → {in, out, responsibles}
        pos_grouped = {}
        for r in rows:
            create_dt = r['create_dt']
            s_start = r['session_start']
            s_stop = r['session_stop']

            if s_start and s_stop and (s_start <= create_dt <= s_stop):
                target_date = create_dt.date()
            elif s_start:
                target_date = s_start.date()
            else:
                target_date = create_dt.date()

            if not (self.start_date <= target_date <= self.end_date):
                continue

            payment_ref = (r['payment_ref'] or '').lower()
            is_in = '-in-' in payment_ref
            amt = abs(r['amount'] or 0.0)
            resp = r['responsible_person'] or 'Unknown'
            gk = (target_date, r['clinic_id'])

            if gk not in pos_grouped:
                # clinic_name from SQL may be JSONB dict in some DBs; normalise
                cname = r['clinic_name']
                if isinstance(cname, dict):
                    cname = cname.get('en_US') or next(iter(cname.values()), '')
                pos_grouped[gk] = {
                    'in': 0.0,
                    'out': 0.0,
                    'responsibles': set(),
                    'clinic_name': cname or '',
                }

            if is_in:
                pos_grouped[gk]['in'] += amt
            else:
                pos_grouped[gk]['out'] += amt

            if resp:
                pos_grouped[gk]['responsibles'].add(resp)

        # ── 4. Merge all keys ──
        pos_keys = set(pos_grouped.keys())
        bank_mapped_keys = {k for k in bank_data if isinstance(k[1], int)}
        bank_no_clinic_keys = {k for k in bank_data if k[1] == NO_CLINIC_KEY}
        bank_unmapped_keys = {
            k for k in bank_data
            if isinstance(k[1], str) and k[1] != NO_CLINIC_KEY
        }

        all_mapped_keys = pos_keys | bank_mapped_keys

        if not all_mapped_keys and not bank_unmapped_keys and not bank_no_clinic_keys:
            return self._notify(
                "Zero Records",
                "No transactions found in the selected date range.",
                'warning'
            )

        # Audit header is created as the real user (for run_by tracking)
        audit = self.env['cash.deposit.audit'].create({
            'name': f"Cash Deposit Comparison ({self.start_date} \u2013 {self.end_date})",
            'start_date': self.start_date,
            'end_date': self.end_date,
            'file_name': self.file_name,
        })

        lines_to_create = []

        # ── A. Mapped clinic lines (POS + Bank) ──
        for date_key, clinic_id in all_mapped_keys:
            info = pos_grouped.get(
                (date_key, clinic_id),
                {'in': 0.0, 'out': 0.0, 'responsibles': set(), 'clinic_name': ''}
            )
            bank_amt = bank_data.get((date_key, clinic_id), 0.0)

            # .sudo().browse so name resolves even if user has no access to that clinic
            clinic = self.env['clinic.clinic'].sudo().browse(clinic_id)
            if clinic.exists():
                clinic_name = clinic.name
            else:
                clinic_name = info.get('clinic_name') or 'Unknown'

            expected = info['out'] - info['in']
            diff = expected - bank_amt

            lines_to_create.append({
                'audit_id': audit.id,
                'audit_date': date_key,
                'clinic_id': clinic_id,
                'clinic_display': clinic_name,
                'pos_cash_in': info['in'],
                'pos_cash_out': info['out'],
                'total_expected_deposit': expected,
                'bank_received': bank_amt,
                'difference': diff,
                'responsible_person': ", ".join(sorted(info['responsibles'])) if info['responsibles'] else '',
            })

        # ── B. Clinic column was BLANK in CSV ──
        for date_key, _ in bank_no_clinic_keys:
            bank_amt = bank_data.get((date_key, NO_CLINIC_KEY), 0.0)
            lines_to_create.append({
                'audit_id': audit.id,
                'audit_date': date_key,
                'clinic_id': False,
                'clinic_display': '(No Clinic Mentioned in CSV)',
                'pos_cash_in': 0.0,
                'pos_cash_out': 0.0,
                'total_expected_deposit': 0.0,
                'bank_received': bank_amt,
                'difference': -bank_amt,
                'responsible_person': '',
            })

        # ── C. Clinic text present but NOT mapped in config ──
        for date_key, raw_name in bank_unmapped_keys:
            bank_amt = bank_data.get((date_key, raw_name), 0.0)
            lines_to_create.append({
                'audit_id': audit.id,
                'audit_date': date_key,
                'clinic_id': False,
                'clinic_display': f'{raw_name} (Unmapped in Config)',
                'pos_cash_in': 0.0,
                'pos_cash_out': 0.0,
                'total_expected_deposit': 0.0,
                'bank_received': bank_amt,
                'difference': -bank_amt,
                'responsible_person': '',
            })

        self.env['cash.deposit.audit.line'].create(lines_to_create)

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'cash.deposit.audit',
            'view_mode': 'form',
            'res_id': audit.id,
            'target': 'current',
        }

    def _notify(self, title, message, ntype):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': ntype},
        }