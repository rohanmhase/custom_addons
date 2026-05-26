from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class ResUsersInherit(models.Model):
    _inherit = 'res.users'
    clinic_id = fields.Many2one('clinic.clinic', string='Assigned Clinic')


class Clinic(models.Model):
    _inherit = 'clinic.clinic'

    allocation_ids = fields.One2many('operational.fund.allocation', 'clinic_id', string='Allocations')
    disbursement_ids = fields.One2many('operational.fund.disbursement', 'clinic_id', string='Disbursements')

    total_allocated = fields.Float(string='Total Allocated', compute='_compute_balances', store=True)
    total_spent = fields.Float(string='Total Disbursed', compute='_compute_balances', store=True)
    op_fund_balance = fields.Float(string='Available Balance', compute='_compute_balances', store=True)

    # REMOVED: approval_threshold is completely gone from here!

    @api.depends('allocation_ids.amount', 'disbursement_ids.amount', 'disbursement_ids.state')
    def _compute_balances(self):
        for clinic in self:
            allocated = sum(clinic.allocation_ids.mapped('amount'))
            spent = sum(clinic.disbursement_ids.filtered(lambda t: t.state == 'approved').mapped('amount'))
            clinic.total_allocated = allocated
            clinic.total_spent = spent
            clinic.op_fund_balance = allocated - spent


# NEW: Standalone Configuration Model strictly inside this module
class OperationalFundConfig(models.Model):
    _name = 'operational.fund.config'
    _description = 'Operational Fund Configuration'
    _rec_name = 'clinic_id'

    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, ondelete='cascade')
    approval_threshold = fields.Float(
        string='Auto-Approval Threshold',
        default=0.0,
        help="Disbursements at or below this amount are automatically approved."
    )

    _sql_constraints = [
        ('clinic_uniq', 'unique (clinic_id)', 'A clinic can only have one operational fund configuration limit!')
    ]


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

    # The date the allocation was physically recorded
    date = fields.Date(string='Log Date', default=fields.Date.context_today, required=True)

    # NEW: Strict Date bounds for the allocation period
    period_start = fields.Date(string='Period Start', required=True)
    period_end = fields.Date(string='Period End', required=True)

    controller_id = fields.Many2one('res.users', string='Allocated By', default=lambda self: self.env.user,
                                    readonly=True)

    # NEW: Guard rail to prevent impossible time travel
    @api.constrains('period_start', 'period_end')
    def _check_period_dates(self):
        for rec in self:
            if rec.period_start and rec.period_end and rec.period_start > rec.period_end:
                raise ValidationError(_("The Period End date cannot be earlier than the Period Start date."))


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

    payee_id = fields.Many2one('hr.employee', string='Payee / Vendor', required=True, tracking=True,
                               ondelete='restrict')
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

    signed_voucher_file = fields.Binary(string='Signed Voucher (Upload)')
    signed_voucher_filename = fields.Char(string='Signed Voucher Filename')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('waiting', 'Waiting Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], string='Status', default='draft', tracking=True)

    @api.onchange('amount', 'clinic_id')
    def _onchange_budget_warning(self):
        if self.clinic_id and self.amount > 0:
            available_balance = self.clinic_id.op_fund_balance
            if self.amount > available_balance:
                return {
                    'warning': {
                        'title': "Insufficient Funds!",
                        'message': f"This request (₹{self.amount}) exceeds the available balance (₹{available_balance}) for {self.clinic_id.name}."
                    }
                }
            elif available_balance > 0 and (self.amount / available_balance) >= 0.90:
                return {
                    'warning': {
                        'title': "Low Balance Alert",
                        'message': f"Warning: This disbursement will consume 90%+ of the remaining funds for {self.clinic_id.name}. Proceed with caution."
                    }
                }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('operational.fund.disbursement') or 'New'
        return super().create(vals_list)

    def action_print_voucher(self):
        report = self.env['ir.actions.report'].search(
            [('report_name', '=', 'operational_fund.report_voucher_template')], limit=1)
        if report:
            return report.report_action(self)
        return False

    def action_submit_for_approval(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_("Disbursement amount must be strictly positive."))
            if not rec.signed_voucher_file:
                raise ValidationError(
                    _("Hold on! You must download, sign, and upload the Disbursement Voucher before you can submit it."))

            # NEW LOGIC: Look up the threshold from our new isolated configuration table!
            config = self.env['operational.fund.config'].search([('clinic_id', '=', rec.clinic_id.id)], limit=1)
            threshold = config.approval_threshold if config else 0.0

            if threshold > 0 and rec.amount <= threshold:
                if rec.amount > rec.clinic_id.op_fund_balance:
                    raise ValidationError(
                        _("Cannot auto-approve. Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % rec.clinic_id.op_fund_balance)
                rec.state = 'approved'
                rec.message_post(
                    body=f"System Auto-Approved: The requested amount (₹{rec.amount}) is within the clinic's safe threshold of ₹{threshold}.",
                    subtype_xmlid='mail.mt_note',
                    author_id=self.env.ref('base.partner_root').id  # This posts the message as OdooBot!
                )
            else:
                rec.state = 'waiting'

    def action_approve(self):
        for rec in self:
            if rec.amount > rec.clinic_id.op_fund_balance:
                raise ValidationError(
                    _("Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % rec.clinic_id.op_fund_balance)
            rec.state = 'approved'

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = 'draft'

    @api.constrains('amount', 'state')
    def _check_balance(self):
        for rec in self:
            if rec.state == 'approved' and rec.amount > (rec.clinic_id.op_fund_balance + rec.amount):
                raise ValidationError(_("Cannot approve. This disbursement exceeds the available clinic balance."))

    def unlink(self):
        for rec in self:
            raise ValidationError(
                _("Auditing Security: Disbursement vouchers cannot be deleted once created. If a mistake was made, please use the 'Reject' or 'Reset to Draft' workflow instead."))
        return super().unlink()