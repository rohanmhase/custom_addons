from odoo import models, fields, api
from odoo.exceptions import UserError

class TelemedicineInvoice(models.Model):
    _name = 'telemedicine.invoice'
    _description = 'Telemedicine Invoice (Standalone)'
    _order = 'invoice_date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(string='Invoice Number', required=True, readonly=True, copy=False)
    customer_id = fields.Many2one(
        'telemedicine.customer', string='Customer',
        required=True, ondelete='restrict',
    )
    invoice_date = fields.Date(string='Invoice Date', required=True)
    purchase_order = fields.Char(string='Purchase Order')
    place_of_supply = fields.Char(string='Place of Supply')
    delivered = fields.Char(string='Delivery Status')

    amount_total = fields.Float(string='Total (from CSV)', required=True, digits=(16, 2))
    tax_percent = fields.Float(string='Tax %', default=5.0, digits=(12, 4))
    amount_untaxed = fields.Float(string='Untaxed', compute='_compute_amounts', store=True, digits=(16, 2))
    amount_tax = fields.Float(string='Tax', compute='_compute_amounts', store=True, digits=(16, 2))
    rounding_adj = fields.Float(string='Rounding Adj.', compute='_compute_amounts', store=True, digits=(16, 2))
    amount_printed_total = fields.Float(string='Printed Total', compute='_compute_amounts', store=True, digits=(16, 2))
    amount_due = fields.Float(string='Amount Due', compute='_compute_amount_due')
    amount_total_words = fields.Char(string='Total in Words', compute='_compute_amount_total_words')
    amount_residual = fields.Float(string='Amount Residual', compute='_compute_amount_due')

    narration = fields.Text(
        string='Notes / Narration',
        compute='_compute_narration', store=True, readonly=False,
    )
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted')],
        string='Status', default='posted', required=True, copy=False,
    )
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', string='Currency', required=True,
        default=lambda self: self.env.company.currency_id,
    )

    _sql_constraints = [
        ('unique_name_company', 'unique(name, company_id)',
         'An invoice with this number already exists for this company.'),
    ]

    @api.depends('amount_total', 'tax_percent')
    def _compute_amounts(self):
        for inv in self:
            total = inv.amount_total
            pct = inv.tax_percent or 0.0
            if pct:
                untaxed = round(total / (1 + pct / 100.0), 2)
                tax = round(untaxed * pct / 100.0, 2)
            else:
                untaxed = round(total, 2)
                tax = 0.0
            rounding = round(total - (untaxed + tax), 2)
            inv.amount_untaxed = untaxed
            inv.amount_tax = tax
            inv.rounding_adj = rounding
            inv.amount_printed_total = round(untaxed + tax + rounding, 2)

    def _compute_amount_due(self):
        for inv in self:
            inv.amount_due = 0.0
            inv.amount_residual = 0.0

    @api.depends('amount_printed_total', 'currency_id')
    def _compute_amount_total_words(self):
        for inv in self:
            try:
                inv.amount_total_words = inv.currency_id.amount_to_text(inv.amount_printed_total)
            except Exception:
                inv.amount_total_words = False

    @api.depends('delivered')
    def _compute_narration(self):
        for inv in self:
            inv.narration = inv.delivered or ''

    def get_document_title(self):
        """Same rule as report_invoice_document_bill_of_supply."""
        self.ensure_one()
        has_tax = bool(self.tax_percent and self.amount_tax)
        return 'Tax Invoice' if has_tax else 'Bill of Supply'

    def _get_report_line(self):
        """Single synthetic line dict for PDF."""
        self.ensure_one()
        config = self.env['telemedicine.import.config']._get_config()
        return {
            'name': config.get_line_label(),
            'hsn': config.get_hsn_code() or '',
            'quantity': 1.0,
            'uom': 'Sessions',
            'price_unit': self.amount_untaxed,
            'tax_label': f'{self.tax_percent:g}%' if self.tax_percent else '',
            'price_subtotal': self.amount_untaxed,
        }

    def _get_payments_vals(self):
        """Fake payment widget row (historical = fully paid)."""
        self.ensure_one()
        if self.state != 'posted':
            return []
        return [{
            'is_exchange': 0,
            'date': self.invoice_date,
            'amount': self.amount_printed_total,
            'pos_payment_name': False,
            'journal_name': False,
        }]

    def _get_hsn_summary(self):
        """Return list of HSN summary rows for the PDF."""
        self.ensure_one()
        config = self.env['telemedicine.import.config']._get_config()
        hsn_code = config.get_hsn_code()
        if not hsn_code:
            return []
        half_tax = self.amount_tax / 2.0
        return [{
            'hsn_code': hsn_code,
            'quantity': 1.0,
            'uom': 'Sessions',
            'rate': self.tax_percent,
            'taxable_value': self.amount_untaxed,
            'sgst_amount': half_tax,
            'cgst_amount': half_tax,
            'igst_amount': 0.0,
            'total_tax': self.amount_tax,
        }]

    def get_half_tax(self):
        """Returns half of amount_tax (for SGST/CGST split)."""
        self.ensure_one()
        return self.amount_tax / 2.0

    def action_post(self):
        self.write({'state': 'posted'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_print_invoice(self):
        self.ensure_one()
        report = self.env.ref(
            'telemedicine_invoice.action_report_tm_invoice',
            raise_if_not_found=False,
        )
        if not report:
            report = self.env['ir.actions.report'].search(
                [('report_name', '=', 'telemedicine_invoice.report_tm_invoice')],
                limit=1,
            )
        if not report:
            raise UserError(
                'The Telemedicine invoice report is not loaded. '
                'Upgrade the module: Apps > Telemedicine Invoice > Upgrade.'
            )
        return report.report_action(self)