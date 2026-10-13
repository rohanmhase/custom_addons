import re
import base64
from odoo import models, fields, api
from odoo.exceptions import UserError, ValidationError
from odoo.tools import file_open

try:
    from num2words import num2words
except ImportError:
    num2words = None

# Max invoices per "separate PDFs (ZIP)" download (each PDF is rendered one by one)
MAX_SEPARATE_PDFS = 50


class MybillbookSoftDelete(models.AbstractModel):
    """3-level delete: active -> (Delete) archived -> (Delete) hidden from UI, kept in DB."""
    _name = 'mybillbook.softdelete.mixin'
    _description = 'My Bill Book Soft Delete'

    active = fields.Boolean(default=True)
    ui_deleted = fields.Boolean('Deleted from UI', default=False, index=True)

    def unlink(self):
        self.check_access_rights('unlink')
        self.check_access_rule('unlink')
        to_archive = self.filtered('active')
        to_archive.write({'active': False})
        (self - to_archive).write({'ui_deleted': True, 'active': False})
        return True

    def action_archive_custom(self):
        self.write({'active': False})

    def action_unarchive_custom(self):
        self.write({'active': True})


class MybillbookClinic(models.Model):
    _name = 'mybillbook.clinic'
    _inherit = ['mybillbook.softdelete.mixin']
    _description = 'My Bill Book Clinic Details'

    name = fields.Char('Clinic Name', required=True)
    logo = fields.Image('Clinic Logo')
    address = fields.Text('Address')
    mobile = fields.Char('Mobile')
    gstin = fields.Char('GSTIN')
    place_of_supply = fields.Char('Place of Supply')
    bank_name = fields.Char('Bank Name')
    bank_account = fields.Char('Account No')
    bank_ifsc = fields.Char('IFSC Code')
    bank_holder_name = fields.Char('Account Holder Name')
    @api.constrains('name')
    def _check_name(self):
        for rec in self:
            name = (rec.name or '').strip()
            if not name:
                raise ValidationError("Clinic name cannot be empty.")
            if rec.ui_deleted:
                continue
            dup = self.with_context(active_test=False).search_count([
                ('name', '=', rec.name),
                ('ui_deleted', '=', False),
                ('id', '!=', rec.id),
            ])
            if dup:
                raise ValidationError(
                    "A clinic named '%s' already exists. Clinic names must be unique "
                    "because invoices are linked by name." % rec.name)

    @api.constrains('mobile')
    def _check_mobile(self):
        for rec in self:
            if rec.mobile and not re.fullmatch(r'\d{1,10}', rec.mobile):
                raise ValidationError(
                    "Clinic mobile number must contain digits only and be at most 10 digits "
                    "(no spaces, '+', or country code).")

    @api.constrains('gstin', 'bank_ifsc', 'bank_account')
    def _check_bank_gst(self):
        for rec in self:
            if rec.gstin and not re.fullmatch(
                    r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', rec.gstin.upper()):
                raise ValidationError("GSTIN must be a valid 15-character GSTIN.")
            if rec.bank_ifsc and not re.fullmatch(r'[A-Za-z]{4}0[A-Za-z0-9]{6}', rec.bank_ifsc):
                raise ValidationError("IFSC code must be 11 characters (e.g. HDFC0001234).")
            if rec.bank_account and not re.fullmatch(r'\d{6,18}', rec.bank_account):
                raise ValidationError("Account number must be 6 to 18 digits, numbers only.")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._relink_invoices()
        return records

    def write(self, vals):
        old_names = self.mapped('name') if 'name' in vals else []
        res = super().write(vals)
        if {'name', 'active', 'ui_deleted'} & set(vals):
            self._relink_invoices(extra_names=old_names)
        return res

    def _relink_invoices(self, extra_names=()):
        """Re-attach invoices whose imported clinic name matches these clinics."""
        names = set(self.mapped('name')) | set(extra_names)
        if not names:
            return
        invoices = self.env['mybillbook.invoice'].sudo().with_context(active_test=False).search(
            [('clinic_name', 'in', list(names))])
        if invoices:
            invoices.modified(['clinic_name'])


class MybillbookInvoice(models.Model):
    _name = 'mybillbook.invoice'
    _inherit = ['mybillbook.softdelete.mixin']
    _description = 'My Bill Book Invoice Archive'
    _rec_name = 'invoice_no'
    _order = 'date desc, invoice_no desc'

    invoice_no = fields.Char('Invoice No', readonly=True, index=True)
    date = fields.Date('Date', readonly=True)
    party_name = fields.Char('Party Name', readonly=True, index=True)
    party_mobile = fields.Char('Party Mobile', readonly=True)
    payment_mode = fields.Char('Payment Mode', readonly=True)
    clinic_name = fields.Char('Clinic (Imported)', readonly=True, index=True)

    clinic_id = fields.Many2one('mybillbook.clinic', string='Clinic Details', compute='_compute_clinic', store=True)
    line_ids = fields.One2many('mybillbook.invoice.line', 'invoice_id', string='Items', readonly=True)

    total_amount = fields.Float('Total Amount', compute='_compute_total', store=True)
    total_qty = fields.Float('Total Qty', compute='_compute_total', store=True)
    amount_in_words = fields.Char('Amount in Words', compute='_compute_words')
    notes = fields.Text('Notes', readonly=True)

    def _get_logo_uri(self):
        """Same logo for every clinic: static/src/img/logo.png"""
        with file_open('mybillbook_invoices/static/src/img/logo.png', 'rb') as f:
            return 'data:image/png;base64,' + base64.b64encode(f.read()).decode()

    def pdf_filename(self):
        """'<invoice no> - <party name>' made safe for use as a file name."""
        self.ensure_one()
        name = '%s - %s' % (self.invoice_no or 'Invoice', self.party_name or '')
        name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '-', name).strip(' .-')
        return name or 'Invoice'

    def action_download_separate(self):
        """One PDF per invoice, delivered as a ZIP (a single invoice comes as a plain PDF)."""
        if not self:
            raise UserError("Select at least one invoice.")
        if len(self) > MAX_SEPARATE_PDFS:
            raise UserError("Please select at most %d invoices for a separate-PDF download "
                            "(you selected %d)." % (MAX_SEPARATE_PDFS, len(self)))
        return {
            'type': 'ir.actions.act_url',
            'url': '/mybillbook/download_separate?ids=%s' % ','.join(str(i) for i in self.ids),
            'target': 'self',
        }

    @api.depends('line_ids.amount', 'line_ids.quantity_num')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('amount'))
            rec.total_qty = sum(rec.line_ids.mapped('quantity_num'))

    @api.depends('clinic_name')
    def _compute_clinic(self):
        Clinic = self.env['mybillbook.clinic'].sudo().with_context(active_test=False)
        names = {r.clinic_name for r in self if r.clinic_name}
        best = {}
        for c in Clinic.search([('name', 'in', list(names))]).sorted(
                key=lambda c: (c.ui_deleted, not c.active, -c.id)):
            best.setdefault(c.name, c)
        for rec in self:
            rec.clinic_id = best[rec.clinic_name].id if rec.clinic_name in best else False

    @api.depends('total_amount')
    def _compute_words(self):
        for rec in self:
            if num2words:
                try:
                    words = num2words(int(rec.total_amount), lang='en_IN').title()
                    rec.amount_in_words = f"{words} Rupees"
                except Exception:
                    rec.amount_in_words = str(rec.total_amount)
            else:
                rec.amount_in_words = str(rec.total_amount)


class MybillbookInvoiceLine(models.Model):
    _name = 'mybillbook.invoice.line'
    _description = 'My Bill Book Invoice Line'

    invoice_id = fields.Many2one('mybillbook.invoice', ondelete='cascade')
    item_name = fields.Char('Item Name')
    quantity_str = fields.Char('Quantity')
    quantity_num = fields.Float('Quantity Num')
    rate = fields.Float('Price/Unit')
    amount = fields.Float('Amount')
    quantity_display = fields.Char('Qty (Print)', compute='_compute_quantity_display')

    @api.depends('quantity_str', 'quantity_num')
    def _compute_quantity_display(self):
        for line in self:
            s = (line.quantity_str or '').strip()
            m = re.match(r'^([\d.,]+)\s*(.*)$', s)
            if m:
                try:
                    num = float(m.group(1).replace(',', ''))
                    s = '%g' % num
                    if m.group(2):
                        s += ' ' + m.group(2)
                except ValueError:
                    pass
            line.quantity_display = s
