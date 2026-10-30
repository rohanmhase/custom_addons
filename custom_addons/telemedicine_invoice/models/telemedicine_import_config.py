from odoo import models, fields, api


class TelemedicineImportConfig(models.Model):
    _name = 'telemedicine.import.config'
    _description = 'Telemedicine Import Configuration'

    name = fields.Char(string='Config Name', default='Default Config', required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )
    invoice_product_id = fields.Many2one(
        'product.product', string='Invoice Line Product',
        domain=[('sale_ok', '=', True)],
        required=True,
        help='Product name + HSN shown on the invoice PDF line.',
    )
    default_tax_percent = fields.Float(
        string='Default Tax %', default=5.0, digits=(12, 4),
        help='Applied when CSV has no tax column.',
    )
    show_narration_on_pdf = fields.Boolean(
        string='Show Notes on Invoice PDF',
        default=True,
        help='If off, the Notes / Delivery text will not be printed on the PDF.',
    )

    _sql_constraints = [
        ('unique_company', 'unique(company_id)',
         'Only one config per company is allowed.'),
    ]

    @api.model
    def _get_config(self):
        company = self.env.company
        config = self.search([('company_id', '=', company.id)], limit=1)
        if not config:
            config = self.sudo().create({
                'name': 'Default Config',
                'company_id': company.id,
            })
        return config

    def get_line_label(self):
        self.ensure_one()
        if self.invoice_product_id:
            return self.invoice_product_id.display_name
        return ''

    def get_hsn_code(self):
        self.ensure_one()
        product = self.invoice_product_id
        if product and 'l10n_in_hsn_code' in product._fields:
            return product.l10n_in_hsn_code or False
        return False