from odoo import models, fields, api
from odoo.exceptions import UserError
import base64
import csv
import io


class EmiProviderConfig(models.Model):
    _name = 'emi.provider.config'
    _description = 'EMI Provider Configuration'
    _rec_name = 'name'
    _inherit = ['emi.soft.delete.mixin']

    name = fields.Char(string='Provider Name', required=True)
    match_mode = fields.Selection([
        ('invoice', 'Match by Invoice Number'),
        ('clinic_name', 'Match by Clinic Name'),
    ], string='Match Method', required=True, default='invoice')

    payment_method_ids = fields.Many2many(
        'pos.payment.method',
        string='POS Payment Methods'
    )

    col_identifier = fields.Char(string='Match Column Header', required=True)
    col_total_loan = fields.Char(string='Total Loan Header', required=True)
    col_advance = fields.Char(string='Advance Header')
    col_final_payout = fields.Char(string='Final Payout Header', required=True)
    col_fee_1 = fields.Char(string='Fee Header 1')
    col_fee_2 = fields.Char(string='Fee Header 2')
    col_settlement_date = fields.Char(string='Settlement Date Header', required=True)

    clinic_mapping_ids = fields.One2many(
        'emi.clinic.mapping',
        'provider_id',
        string='Clinic Mappings'
    )

    def action_export_clinic_mappings(self):
        self.ensure_one()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['csv_clinic_name', 'odoo_clinic_name'])

        for m in self.clinic_mapping_ids:
            writer.writerow([
                m.csv_clinic_name or '',
                m.clinic_id.name if m.clinic_id else '',
            ])

        data = base64.b64encode(output.getvalue().encode('utf-8'))
        filename = f"{self.name}_clinic_mappings.csv".replace(' ', '_')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': data,
            'mimetype': 'text/csv',
            'res_model': self._name,
            'res_id': self.id,
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }

    def action_import_clinic_mappings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Import Clinic Mappings',
            'res_model': 'emi.clinic.mapping.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_provider_id': self.id},
        }


class EmiClinicMapping(models.Model):
    _name = 'emi.clinic.mapping'
    _description = 'EMI CSV Clinic Name Mapping'
    _inherit = ['emi.soft.delete.mixin']

    provider_id = fields.Many2one(
        'emi.provider.config',
        required=True,
        ondelete='cascade'
    )
    csv_clinic_name = fields.Char(string='CSV Clinic Name', required=True)
    clinic_id = fields.Many2one(
        'pos.config',
        string='Odoo Clinic',
        required=True
    )


class EmiClinicMappingImportWizard(models.TransientModel):
    _name = 'emi.clinic.mapping.import.wizard'
    _description = 'Import EMI Clinic Mappings'

    provider_id = fields.Many2one(
        'emi.provider.config',
        string='Provider',
        required=True,
        readonly=True
    )
    file_data = fields.Binary(string='CSV File', required=True)
    file_name = fields.Char(string='File Name')
    replace_all = fields.Boolean(string='Replace All Existing', default=False)

    def action_import(self):
        self.ensure_one()
        provider = self.provider_id

        try:
            content = base64.b64decode(self.file_data).decode('utf-8-sig')
        except Exception:
            raise UserError('Could not read file. Upload a valid CSV.')

        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            raise UserError('CSV is empty or has no headers.')

        headers = {h.strip().lower(): h for h in reader.fieldnames}
        csv_col = (
            headers.get('csv_clinic_name') or headers.get('csv clinic name') or
            headers.get('clinic name') or headers.get('center name') or
            headers.get('merchant') or headers.get('shopname')
        )
        odoo_col = (
            headers.get('odoo_clinic_name') or headers.get('odoo clinic name') or
            headers.get('odoo clinic') or headers.get('clinic')
        )

        if not csv_col or not odoo_col:
            raise UserError(
                'CSV must have these headers:\n'
                '  csv_clinic_name, odoo_clinic_name\n\n'
                f'Found: {", ".join(reader.fieldnames)}'
            )

        clinics = self.env['pos.config'].search([])
        clinic_by_name = {c.name.strip().lower(): c.id for c in clinics}
        rows = list(reader)
        if not rows:
            raise UserError('CSV has headers but no data rows.')

        if self.replace_all:
            # Soft delete old mappings
            provider.clinic_mapping_ids.unlink()

        existing = {
            m.csv_clinic_name.strip().lower(): m
            for m in provider.clinic_mapping_ids
        }

        created = updated = skipped = 0
        errors = []

        for i, row in enumerate(rows, start=2):
            csv_name = (row.get(csv_col) or '').strip()
            odoo_name = (row.get(odoo_col) or '').strip()

            if not csv_name and not odoo_name:
                continue
            if not csv_name or not odoo_name:
                errors.append(f'Row {i}: missing csv name or odoo clinic')
                skipped += 1
                continue

            clinic_id = clinic_by_name.get(odoo_name.lower())
            if not clinic_id:
                errors.append(f'Row {i}: Odoo clinic not found → "{odoo_name}"')
                skipped += 1
                continue

            key = csv_name.lower()
            if key in existing:
                existing[key].write({
                    'csv_clinic_name': csv_name,
                    'clinic_id': clinic_id,
                    'active': True,
                    'is_purged': False,
                })
                updated += 1
            else:
                new_map = self.env['emi.clinic.mapping'].create({
                    'provider_id': provider.id,
                    'csv_clinic_name': csv_name,
                    'clinic_id': clinic_id,
                })
                existing[key] = new_map
                created += 1

        msg = f'Import done. Created: {created}, Updated: {updated}, Skipped: {skipped}.'
        if errors:
            msg += '\n\nIssues:\n' + '\n'.join(errors[:20])
            if len(errors) > 20:
                msg += f'\n... and {len(errors) - 20} more.'

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Clinic Mapping Import',
                'message': msg,
                'type': 'success' if not errors else 'warning',
                'sticky': bool(errors),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }