import csv
import base64
import io
from odoo import fields, models, api, _
from odoo.exceptions import UserError

class PsmrReconciliationWizard(models.TransientModel):
    _name = 'psmr.reconciliation.wizard'
    _description = 'PSMR Reconciliation Wizard'

    date_from = fields.Date(string='Start Date', required=True)
    date_to = fields.Date(string='End Date', required=True)
    csv_file = fields.Binary(string='PSMR CSV File', required=True)
    csv_filename = fields.Char(string='CSV Filename')

    def action_reconcile(self):
        self.ensure_one()

        try:
            csv_data = base64.b64decode(self.csv_file).decode('utf-8')
            csv_file_input = io.StringIO(csv_data)
            reader = csv.DictReader(csv_file_input)
        except Exception as e:
            raise UserError(_("Invalid file format. Please upload a valid CSV UTF-8 file.\nDetails: %s", str(e)))

        psmr_sales = {}
        for row in reader:
            clinic_raw = row.get('Clinic')
            amount_raw = row.get('amount')

            if clinic_raw is None or amount_raw is None:
                raise UserError(_("CSV columns must contain exactly 'Clinic' and 'amount' headers."))

            clinic_name = clinic_raw.strip()
            amount_str = amount_raw.strip().replace(',', '')

            if amount_str == '-' or not amount_str:
                amount = 0.0
            else:
                try:
                    amount = float(amount_str)
                except ValueError:
                    amount = 0.0

            psmr_sales[clinic_name] = psmr_sales.get(clinic_name, 0.0) + amount

        query = """
            SELECT 
                pc.id AS pos_config_id,
                SUM(
                    CASE 
                        WHEN pay.row_num = 1 THEN 
                            CASE 
                                WHEN am.move_type = 'out_refund' THEN -ABS(am.amount_total)
                                ELSE ABS(am.amount_total)
                            END
                        ELSE 0
                    END
                ) AS total_sales
            FROM account_move am
            LEFT JOIN pos_order po ON po.account_move = am.id
            LEFT JOIN pos_session ps ON ps.id = po.session_id
            LEFT JOIN pos_config pc ON pc.id = ps.config_id
            LEFT JOIN (
                SELECT 
                    am_inner.id AS inv_id,
                    ROW_NUMBER() OVER (PARTITION BY am_inner.id ORDER BY COALESCE(ppm.name->>'en_US', aj.name->>'en_US')) as row_num
                FROM account_move am_inner
                JOIN account_move_line aml_inv ON aml_inv.move_id = am_inner.id
                JOIN account_account acc ON acc.id = aml_inv.account_id
                JOIN account_partial_reconcile apr ON (apr.debit_move_id = aml_inv.id OR apr.credit_move_id = aml_inv.id)
                JOIN account_move_line aml_pay ON (
                    (aml_pay.id = apr.credit_move_id AND aml_pay.id <> aml_inv.id) OR 
                    (aml_pay.id = apr.debit_move_id AND aml_pay.id <> aml_inv.id)
                )
                JOIN account_journal aj ON aj.id = aml_pay.journal_id
                LEFT JOIN pos_payment pp ON pp.account_move_id = aml_pay.move_id
                LEFT JOIN pos_payment_method ppm ON ppm.id = pp.payment_method_id
                WHERE am_inner.state = 'posted'
                  AND acc.account_type = 'asset_receivable'
            ) pay ON pay.inv_id = am.id
            WHERE am.move_type IN ('out_invoice', 'out_refund')
              AND am.state = 'posted'
              AND am.invoice_date >= %s
              AND am.invoice_date <= %s
              AND pc.id IS NOT NULL
            GROUP BY pc.id
        """
        self.env.cr.execute(query, [self.date_from, self.date_to])
        odoo_results = self.env.cr.dictfetchall()

        odoo_sales = {row['pos_config_id']: float(row['total_sales']) for row in odoo_results}

        mappings = self.env['clinic.psmr.mapping'].search([])
        psmr_to_odoo = {m.psmr_name: m.pos_config_id.id for m in mappings}
        odoo_to_psmr = {m.pos_config_id.id: m.psmr_name for m in mappings}

        report_lines = []
        processed_psmr_names = set()
        processed_odoo_ids = set()

        for psmr_name, psmr_amt in psmr_sales.items():
            odoo_config_id = psmr_to_odoo.get(psmr_name)

            if odoo_config_id:
                odoo_amt = odoo_sales.get(odoo_config_id, 0.0)
                report_lines.append({
                    'pos_config_id': odoo_config_id,
                    'psmr_name': psmr_name,
                    'odoo_sales': odoo_amt,
                    'psmr_sales': psmr_amt,
                    'difference': odoo_amt - psmr_amt,
                    'status': 'matched'
                })
                processed_odoo_ids.add(odoo_config_id)
            else:
                report_lines.append({
                    'psmr_name': psmr_name,
                    'odoo_sales': 0.0,
                    'psmr_sales': psmr_amt,
                    'difference': -psmr_amt,
                    'status': 'unmapped_psmr'
                })
            processed_psmr_names.add(psmr_name)

        all_active_configs = self.env['pos.config'].search([])
        for config in all_active_configs:
            if config.id in processed_odoo_ids:
                continue

            odoo_amt = odoo_sales.get(config.id, 0.0)
            mapped_psmr_name = odoo_to_psmr.get(config.id)

            if mapped_psmr_name:
                if mapped_psmr_name not in processed_psmr_names:
                    report_lines.append({
                        'pos_config_id': config.id,
                        'psmr_name': mapped_psmr_name,
                        'odoo_sales': odoo_amt,
                        'psmr_sales': 0.0,
                        'difference': odoo_amt,
                        'status': 'missing_in_psmr'
                    })
            else:
                if odoo_amt != 0.0:
                    report_lines.append({
                        'pos_config_id': config.id,
                        'psmr_name': '⚠️ NO MAPPING CONFIGURED',
                        'odoo_sales': odoo_amt,
                        'psmr_sales': 0.0,
                        'difference': odoo_amt,
                        'status': 'missing_in_psmr'
                    })

        if self.date_from == self.date_to:
            report_title = f"Daily Sales Comparison - {self.date_from.strftime('%d %B %Y')}"
        else:
            report_title = f"Daily Sales Comparison - {self.date_from.strftime('%d %B %Y')} to {self.date_to.strftime('%d %B %Y')}"

        comparison = self.env['daily.sales.comparison'].create({
            'name': report_title,
            'date_from': self.date_from,
            'date_to': self.date_to,
        })

        for line in report_lines:
            line['comparison_id'] = comparison.id
            self.env['daily.sales.comparison.line'].create(line)

        return {
            'name': report_title,
            'type': 'ir.actions.act_window',
            'res_model': 'daily.sales.comparison',
            'view_mode': 'form',
            'res_id': comparison.id,
            'target': 'current',
        }