from odoo import models, fields, api, _
from odoo.exceptions import UserError


class OnlineImportConfig(models.Model):
    _name = 'online.import.config'
    _description = 'Online Import Configuration'

    name = fields.Char(default='Online Imports Settings', required=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        required=True,
        default=lambda self: self.env.company
    )
    prepaid_journal_id = fields.Many2one(
        'account.journal',
        string='Prepaid Bank Journal',
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]"
    )
    pod_journal_id = fields.Many2one(
        'account.journal',
        string='POD Bank Journal',
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]"
    )
    merged_product_id = fields.Many2one(
        'product.product',
        string='Medicine Merged Product',
        help='Product used for medicines line.',
        domain="[('sale_ok', '=', True)]",
    )
    consultation_product_id = fields.Many2one(
        'product.product',
        string='Doctor Consultation Product',
        help='Product used for Doctor Consultation line (0% tax).',
        domain="[('sale_ok', '=', True)]",
    )
    medicine_product_id = fields.Many2one(
        'product.product',
        string='Medicine Product',
        help='Product used for Medicine line after split date (tax applied).',
        domain="[('sale_ok', '=', True)]",
    )
    consultation_amount = fields.Float(
        string='Standard Consultation Fee (₹)',
        default=600.0,
        required=True,
    )
    min_medicine_amount = fields.Float(
        string='Minimum Medicine Amount (₹ Tax Incl.)',
        default=50.0,
        required=True,
    )
    split_start_date = fields.Date(
        string='2-Line Split Effective Date',
        default='2026-11-01',
        required=True,
        help='Invoices on or after this date will be split into Consultation + Medicine.',
    )
    show_items_in_notes = fields.Boolean(
        string='Print Medicine List in PDF Invoice',
        default=True,
    )
    show_company_logo = fields.Boolean(
        string='Print Company Logo on Teleconsult PDF',
        default=True,
        help='If unchecked, company logo is hidden on tele-consultation invoices only.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            comp_id = vals.get('company_id') or self.env.company.id
            if self.search_count([('company_id', '=', comp_id)]) >= 1:
                raise UserError(_("Configuration already exists for this company."))
        return super().create(vals_list)