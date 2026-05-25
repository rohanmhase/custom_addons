from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


# 1. USER INHERITANCE & SECURITY WHITELIST
class ResUsersInherit(models.Model):
    _inherit = 'res.users'

    clinic_id = fields.Many2one('clinic.clinic', string='Assigned Clinic')

    @property
    def SELF_READABLE_FIELDS(self):
        """Whitelists the clinic_id so standard users can actually read it during security checks!"""
        return super().SELF_READABLE_FIELDS + ['clinic_id']


class Clinic(models.Model):
    _inherit = 'clinic.clinic'

    allocation_ids = fields.One2many('operational.fund.allocation', 'clinic_id', string='Allocations')
    disbursement_ids = fields.One2many('operational.fund.disbursement', 'clinic_id', string='Disbursements')

    total_allocated = fields.Float(string='Total Allocated', compute='_compute_balances', store=True)
    total_spent = fields.Float(string='Total Disbursed', compute='_compute_balances', store=True)
    op_fund_balance = fields.Float(string='Available Balance', compute='_compute_balances', store=True)

    @api.depends('allocation_ids.amount', 'disbursement_ids.amount', 'disbursement_ids.state')
    def _compute_balances(self):
        for clinic in self:
            allocated = sum(clinic.allocation_ids.mapped('amount'))
            spent = sum(clinic.disbursement_ids.filtered(lambda t: t.state == 'validated').mapped('amount'))
            clinic.total_allocated = allocated
            clinic.total_spent = spent
            clinic.op_fund_balance = allocated - spent


class OperationalFundAllocation(models.Model):
    _name = 'operational.fund.allocation'
    _description = 'Operational Fund Allocation'
    _inherit = ['mail.thread']

    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Clinic',
        required=True,
        tracking=True,
        default=lambda self: self.env.user.clinic_id.id
    )
    amount = fields.Float(string='Amount Allocated', required=True, tracking=True)
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True)

    # THIS FIELD CAPTURES THE ADMIN
    controller_id = fields.Many2one('res.users', string='Allocated By', default=lambda self: self.env.user,
                                    readonly=True)
    reference = fields.Char(string='Reference Period', help="e.g., May 2026 Allocation")


class OperationalFundDisbursement(models.Model):
    _name = 'operational.fund.disbursement'
    _description = 'Operational Fund Disbursement'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Voucher Number', default='New', readonly=True)

    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Clinic',
        required=True,
        tracking=True,
        default=lambda self: self.env.user.clinic_id.id
    )
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True, tracking=True)

    payee_id = fields.Many2one('hr.employee', string='Payee / Vendor', required=True, tracking=True)
    amount = fields.Float(string='Amount', required=True, tracking=True)

    category = fields.Selection([
        ('therapist_incentive', 'Therapist Incentive'),
        ('therapist_overtime', 'Therapist Overtime'),
        ('home_visit_travel', 'Home Visit Travelling'),
        ('electrician', 'Electrician Charges'),
        ('plumber', 'Plumber Charges'),
        ('stationary', 'Stationary'),
        ('printer_ink', 'Printer Ink'),
        ('cake', 'Cake'),
        ('decorations', 'Decorations'),
        ('carpenter', 'Carpenter Charges'),
        ('cleaning_materials', 'Cleaning Materials'),
        ('biowaste_bags', 'Biowaste Bags'),
        ('other', 'Other Expense')
    ], string='Category', required=True, tracking=True)

    other_expense_details = fields.Char(string='Specify Other Expense', tracking=True)
    description = fields.Text(string='Business Purpose')

    receipt_file = fields.Binary(string='Receipt Attachment')
    receipt_filename = fields.Char(string='Receipt Filename')

    signed_voucher_file = fields.Binary(string='Signed Voucher (Upload)', tracking=True)
    signed_voucher_filename = fields.Char(string='Signed Voucher Filename')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('validated', 'Validated')
    ], string='Status', default='draft', tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('operational.fund.disbursement') or 'New'
        return super().create(vals_list)

    def action_print_voucher(self):
        report = self.env['ir.actions.report'].search([('report_name', '=', 'operational_fund.report_voucher_template')], limit=1)
        if report:
            return report.report_action(self)
        return False

    def action_validate(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_("Disbursement amount must be strictly positive."))
            if rec.amount > rec.clinic_id.op_fund_balance:
                raise ValidationError(_("Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % rec.clinic_id.op_fund_balance)
            if not rec.signed_voucher_file:
                raise ValidationError(_("Hold on! You must download, sign, and upload the Disbursement Voucher before you can validate this record."))
            rec.state = 'validated'

    @api.constrains('amount', 'state')
    def _check_balance(self):
        for rec in self:
            if rec.state == 'validated' and rec.amount > (rec.clinic_id.op_fund_balance + rec.amount):
                raise ValidationError(_("Cannot validate. This disbursement exceeds the available clinic balance."))