from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class OperationalFundAccount(models.Model):
    _name = 'operational.fund.account'
    _description = 'Operational Fund Account'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Account Name', required=True, tracking=True)
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, tracking=True)

    allocation_ids = fields.One2many('operational.fund.allocation', 'account_id', string='Allocations')
    disbursement_ids = fields.One2many('operational.fund.disbursement', 'account_id', string='Disbursements')

    total_allocated = fields.Float(string='Total Allocated', compute='_compute_balances', store=True)
    total_spent = fields.Float(string='Total Disbursed', compute='_compute_balances', store=True)
    current_balance = fields.Float(string='Available Balance', compute='_compute_balances', store=True)

    @api.depends('allocation_ids.amount', 'disbursement_ids.amount', 'disbursement_ids.state')
    def _compute_balances(self):
        for account in self:
            allocated = sum(account.allocation_ids.mapped('amount'))
            spent = sum(account.disbursement_ids.filtered(lambda t: t.state == 'validated').mapped('amount'))

            account.total_allocated = allocated
            account.total_spent = spent
            account.current_balance = allocated - spent


class OperationalFundAllocation(models.Model):
    _name = 'operational.fund.allocation'
    _description = 'Operational Fund Allocation'
    _inherit = ['mail.thread']

    account_id = fields.Many2one('operational.fund.account', string='Account', required=True)
    amount = fields.Float(string='Amount Allocated', required=True, tracking=True)
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True)
    controller_id = fields.Many2one('res.users', string='Allocated By', default=lambda self: self.env.user,
                                    readonly=True)
    reference = fields.Char(string='Reference Period', help="e.g., May 2026 Allocation")


class OperationalFundDisbursement(models.Model):
    _name = 'operational.fund.disbursement'
    _description = 'Operational Fund Disbursement'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Voucher Number', default='New', readonly=True)
    account_id = fields.Many2one('operational.fund.account', string='Account', required=True, tracking=True)
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True, tracking=True)

    payee_id = fields.Many2one('res.partner', string='Payee / Vendor', required=True, tracking=True)
    amount = fields.Float(string='Amount', required=True, tracking=True)

    category = fields.Selection([
        ('travel', 'Travel & Transport'),
        ('utilities', 'Utilities'),
        ('maintenance', 'Facility Maintenance'),
        ('clinic_visit', 'Third-Party Clinic Visit'),
        ('other', 'Other Operational Expense')
    ], string='Category', required=True, tracking=True)

    description = fields.Text(string='Business Purpose')

    receipt_file = fields.Binary(string='Receipt Attachment')
    receipt_filename = fields.Char(string='Receipt Filename')

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

    def action_validate(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_("Disbursement amount must be strictly positive."))
            if rec.amount > rec.account_id.current_balance:
                raise ValidationError(
                    _("Insufficient funds in the operational account! Available balance is ₹%s") % rec.account_id.current_balance)
            rec.state = 'validated'

    @api.constrains('amount', 'state')
    def _check_balance(self):
        for rec in self:
            if rec.state == 'validated' and rec.amount > rec.account_id.current_balance + rec.amount:
                raise ValidationError(_("Cannot validate. This disbursement exceeds the available account balance."))

class ResUsersInherit(models.Model):
    _inherit = 'res.users'

    # This links the logged-in user to a specific clinic for security rules
    clinic_id = fields.Many2one('clinic.clinic', string='Assigned Clinic')