from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class Clinic(models.Model):
    _inherit = 'clinic.clinic'

    allocation_ids = fields.One2many('operational.fund.allocation', 'clinic_id', string='Allocations')
    disbursement_ids = fields.One2many('operational.fund.disbursement', 'clinic_id', string='Disbursements')

    master_fund_id = fields.Many2one('clinic.clinic', string='Master Clinic',
                                     help="If this clinic shares a fund with another, select the main clinic here.")
    child_clinic_ids = fields.One2many('clinic.clinic', 'master_fund_id', string='Child Clinics')

    wallet_group_name = fields.Char(string='Wallet Group', compute='_compute_wallet_group', store=True,
                                    help="Used to group clinics cleanly on the dashboard.")

    total_allocated = fields.Float(string='Total Allocated', compute='_compute_balances', store=True)
    total_spent = fields.Float(string='Total Disbursed', compute='_compute_balances', store=True)
    op_fund_balance = fields.Float(string='Available Balance', compute='_compute_balances', store=True)

    @api.constrains('master_fund_id')
    def _check_master_fund(self):
        for clinic in self:
            if clinic.master_fund_id == clinic:
                raise ValidationError(
                    _("A clinic cannot be its own Master Fund. Please leave the 'Shared Wallet' field blank for the main master clinic."))

    @api.depends('name', 'master_fund_id.name')
    def _compute_wallet_group(self):
        for clinic in self:
            if clinic.master_fund_id:
                clinic.wallet_group_name = clinic.master_fund_id.name
            else:
                clinic.wallet_group_name = clinic.name

    @api.depends('allocation_ids.amount', 'allocation_ids.period_start', 'allocation_ids.period_end',
                 'disbursement_ids.amount', 'disbursement_ids.state', 'disbursement_ids.date',
                 'child_clinic_ids.disbursement_ids.amount', 'child_clinic_ids.disbursement_ids.state',
                 'child_clinic_ids.disbursement_ids.date', 'master_fund_id')
    def _compute_balances(self):
        today = fields.Date.context_today(self)
        for clinic in self:
            if clinic.master_fund_id and clinic.master_fund_id != clinic:
                clinic.total_allocated = 0.0
                clinic.total_spent = 0.0
                clinic.op_fund_balance = 0.0
                continue

            allocations = clinic.allocation_ids.sorted(key=lambda a: a.period_start)
            all_disbursements = clinic.disbursement_ids | clinic.child_clinic_ids.mapped('disbursement_ids')

            # NOTE: "refund_requested" is still technically spent until the manager approves the refund!
            approved_disbs = all_disbursements.filtered(lambda d: d.state in ('approved', 'refund_requested'))

            running_rollover = 0.0
            current_allocated = 0.0
            current_spent = 0.0
            current_balance = 0.0
            found_active = False

            for alloc in allocations:
                period_disbs = approved_disbs.filtered(
                    lambda d: d.date and alloc.period_start <= d.date <= alloc.period_end)
                period_spent = sum(period_disbs.mapped('amount'))

                total_available = running_rollover + alloc.amount
                period_ending_balance = total_available - period_spent

                if alloc.period_start <= today <= alloc.period_end:
                    current_allocated = total_available
                    current_spent = period_spent
                    current_balance = period_ending_balance
                    found_active = True

                running_rollover = period_ending_balance

            if not allocations:
                clinic.total_allocated = 0.0
                clinic.total_spent = 0.0
                clinic.op_fund_balance = 0.0
            elif not found_active and today > allocations[-1].period_end:
                clinic.total_allocated = running_rollover
                clinic.total_spent = 0.0
                clinic.op_fund_balance = running_rollover
            else:
                clinic.total_allocated = current_allocated
                clinic.total_spent = current_spent
                clinic.op_fund_balance = current_balance


class OperationalFundAudit(models.Model):
    _name = 'operational.fund.audit'
    _description = 'Operational Fund Audit Ledger'
    _order = 'date desc, id desc'

    clinic_id = fields.Many2one('clinic.clinic', string='Wallet / Clinic', required=True, readonly=True)
    date = fields.Date(string='Date', required=True, readonly=True)
    transaction_type = fields.Selection([
        ('credit', 'Credit (Allocation In)'),
        ('debit', 'Debit (Disbursement Out)')
    ], string='Type', required=True, readonly=True)
    amount = fields.Float(string='Amount', required=True, readonly=True)
    reference = fields.Char(string='Reference', readonly=True)
    user_id = fields.Many2one('res.users', string='Logged By', readonly=True)


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
    manager_ids = fields.Many2many('res.users', string='Approving Managers',
                                   help="Select all managers (Group, Regional, Head) who can approve and should be notified.")

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
        default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user, 'clinic_id') else False
    )
    amount = fields.Float(string='Amount Allocated', required=True, tracking=True)
    date = fields.Date(string='Log Date', default=fields.Date.context_today, required=True)
    period_start = fields.Date(string='Period Start', required=True)
    period_end = fields.Date(string='Period End', required=True)
    controller_id = fields.Many2one('res.users', string='Allocated By', default=lambda self: self.env.user,
                                    readonly=True)

    @api.constrains('period_start', 'period_end')
    def _check_period_dates(self):
        for rec in self:
            if rec.period_start and rec.period_end and rec.period_start > rec.period_end:
                raise ValidationError(_("The Period End date cannot be earlier than the Period Start date."))

    @api.constrains('period_start', 'period_end', 'clinic_id')
    def _check_overlap(self):
        for rec in self:
            if rec.period_start and rec.period_end:
                domain = [
                    ('id', '!=', rec.id),
                    ('clinic_id', '=', rec.clinic_id.id),
                    ('period_start', '<=', rec.period_end),
                    ('period_end', '>=', rec.period_start)
                ]
                if self.search_count(domain) > 0:
                    raise ValidationError(
                        _("Allocation periods cannot overlap for the same clinic. Please check your dates."))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id,
                'date': rec.date,
                'transaction_type': 'credit',
                'amount': rec.amount,
                'reference': f'Allocation: {rec.period_start} to {rec.period_end}',
                'user_id': self.env.user.id
            })
        return records


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
        default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user, 'clinic_id') else False
    )
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True, tracking=True)

    payee_type = fields.Selection([
        ('internal', 'Internal Employee'),
        ('external', 'External Vendor')
    ], string='Payee Type', default='internal', required=True, tracking=True)

    payee_id = fields.Many2one('hr.employee', string='Internal Employee', tracking=True, ondelete='restrict')
    vendor_name = fields.Char(string='External Vendor Name', tracking=True)
    payee_display = fields.Char(string='Payee', compute='_compute_payee_display', store=True)

    amount = fields.Float(string='Amount', required=True, tracking=True)

    category = fields.Selection([
        ('therapist_incentive', 'Therapist Incentive'),
        ('therapist_overtime', 'Therapist Overtime'),
        ('home_visit_travel', 'Home Visit Travelling'),
        ('electricity', 'Electricity Bill'),
        ('water', 'Water Supply'),
        ('internet', 'Internet / Phone'),
        ('rent', 'Rent'),
        ('electrician', 'Electrician Charges'),
        ('plumber', 'Plumber Charges'),
        ('carpenter', 'Carpenter Charges'),
        ('stationary', 'Stationary'),
        ('printer_ink', 'Printer Ink'),
        ('cleaning_materials', 'Cleaning Materials'),
        ('biowaste_bags', 'Biowaste Bags'),
        ('cake', 'Cake'),
        ('decorations', 'Decorations'),
        ('other', 'Other Expense')
    ], string='Category', required=True, tracking=True)

    other_expense_details = fields.Char(string='Specify Other Expense', tracking=True)
    description = fields.Text(string='Business Purpose')
    receipt_file = fields.Binary(string='Receipt Attachment')
    receipt_filename = fields.Char(string='Receipt Filename')
    signed_voucher_file = fields.Binary(string='Signed Voucher (Upload)')
    signed_voucher_filename = fields.Char(string='Signed Voucher Filename')
    old_signed_voucher_file = fields.Binary(string='Original Signed Voucher (Archived)', readonly=True)
    old_signed_voucher_filename = fields.Char(string='Original Signed Voucher Filename')

    state = fields.Selection([
        ('draft', 'Draft'),
        ('waiting', 'Waiting Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('refund_requested', 'Refund Requested'),  # NEW WORKFLOW STATE
        ('refunded', 'Refunded'),
    ], string='Status', default='draft', tracking=True)

    @api.depends('payee_type', 'payee_id', 'vendor_name')
    def _compute_payee_display(self):
        for rec in self:
            if rec.payee_type == 'internal' and rec.payee_id:
                rec.payee_display = rec.payee_id.name
            elif rec.payee_type == 'external' and rec.vendor_name:
                rec.payee_display = rec.vendor_name
            else:
                rec.payee_display = 'Unknown'

    @api.constrains('payee_type', 'payee_id', 'vendor_name')
    def _check_payee(self):
        for rec in self:
            if rec.payee_type == 'internal' and not rec.payee_id:
                raise ValidationError(_("Please select an Internal Employee."))
            if rec.payee_type == 'external' and not rec.vendor_name:
                raise ValidationError(_("Please specify the External Vendor name."))

    def _verify_active_period(self, active_clinic, check_date):
        if not check_date:
            return True
        allocs = active_clinic.allocation_ids.filtered(lambda a: a.period_start <= check_date <= a.period_end)
        return bool(allocs)

    @api.onchange('amount', 'clinic_id', 'date')
    def _onchange_budget_warning(self):
        if self.clinic_id and self.amount > 0 and self.date:
            active_clinic = self.clinic_id.master_fund_id or self.clinic_id

            if not self._verify_active_period(active_clinic, self.date):
                return {
                    'warning': {
                        'title': "No Active Funding Period!",
                        'message': f"The date selected ({self.date}) does not fall within any funded period for {active_clinic.name}."
                    }
                }

            available_balance = active_clinic.op_fund_balance
            if self.amount > available_balance:
                return {
                    'warning': {
                        'title': "Insufficient Funds!",
                        'message': f"This request (₹{self.amount}) exceeds the available balance (₹{available_balance}) for {active_clinic.name}."
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

            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            if not self._verify_active_period(active_clinic, rec.date):
                raise ValidationError(
                    _("No active funding period found for this date. Please contact management to log this month's allocation."))

            config = self.env['operational.fund.config'].search([('clinic_id', '=', active_clinic.id)], limit=1)
            threshold = config.approval_threshold if config else 0.0

            if threshold > 0 and rec.amount <= threshold:
                if rec.amount > active_clinic.op_fund_balance:
                    raise ValidationError(
                        _("Cannot auto-approve. Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % active_clinic.op_fund_balance)
                rec.action_approve()
                rec.message_post(
                    body=f"System Auto-Approved: The requested amount (₹{rec.amount}) is within the safe threshold of ₹{threshold}.",
                    subtype_xmlid='mail.mt_note',
                    author_id=self.env.ref('base.partner_root').id
                )
            else:
                rec.state = 'waiting'
                if config and config.manager_ids:
                    for manager in config.manager_ids:
                        rec.activity_schedule(
                            'mail.mail_activity_data_todo',
                            user_id=manager.id,
                            summary='Approve Voucher',
                            note=f'Voucher {rec.name} for ₹{rec.amount} exceeds the auto-approval threshold. Please review.'
                        )

    def action_approve(self):
        for rec in self:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            if not self._verify_active_period(active_clinic, rec.date):
                raise ValidationError(
                    _("Cannot approve. This voucher's date does not fall within an active funding period."))

            if rec.amount > active_clinic.op_fund_balance:
                raise ValidationError(
                    _("Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % active_clinic.op_fund_balance)

            rec.state = 'approved'

            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id,
                'date': rec.date,
                'transaction_type': 'debit',
                'amount': rec.amount,
                'reference': f'Disbursement: {rec.name} - {dict(self._fields["category"].selection).get(rec.category)}',
                'user_id': self.env.user.id
            })

            rec.activity_unlink(['mail.mail_activity_data_todo'])

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'
            rec.activity_unlink(['mail.mail_activity_data_todo'])

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == 'approved':
                active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
                self.env['operational.fund.audit'].sudo().create({
                    'clinic_id': active_clinic.id,
                    'date': fields.Date.context_today(self),
                    'transaction_type': 'credit',
                    'amount': rec.amount,
                    'reference': f'Reversal: Reset Approved Voucher {rec.name} to Draft',
                    'user_id': self.env.user.id
                })
                rec.old_signed_voucher_file = rec.signed_voucher_file
                rec.old_signed_voucher_filename = rec.signed_voucher_filename
                rec.signed_voucher_file = False
                rec.signed_voucher_filename = False

            elif rec.state == 'rejected':
                rec.signed_voucher_file = False
                rec.signed_voucher_filename = False
                rec.old_signed_voucher_file = False
                rec.old_signed_voucher_filename = False

            rec.state = 'draft'

    # --- NEW 2-STEP REFUND WORKFLOW ---
    def action_request_refund(self):
        for rec in self:
            if rec.state != 'approved':
                raise ValidationError(_("Only fully approved disbursements can be submitted for a refund."))
            rec.state = 'refund_requested'

            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            config = self.env['operational.fund.config'].search([('clinic_id', '=', active_clinic.id)], limit=1)
            if config and config.manager_ids:
                for manager in config.manager_ids:
                    rec.activity_schedule(
                        'mail.mail_activity_data_todo',
                        user_id=manager.id,
                        summary='Review Refund Request',
                        note=f'A refund has been requested for Voucher {rec.name} (₹{rec.amount}). Please approve or deny.'
                    )

    def action_approve_refund(self):
        for rec in self:
            if rec.state != 'refund_requested':
                raise ValidationError(_("Refund must be requested first."))
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            # Log full recovery entry in the ledger
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id,
                'date': fields.Date.context_today(self),
                'transaction_type': 'credit',
                'amount': rec.amount,
                'reference': f'Refund: Fully Reclaimed Voucher {rec.name}',
                'user_id': self.env.user.id
            })
            rec.state = 'refunded'
            rec.activity_unlink(['mail.mail_activity_data_todo'])

    def action_cancel_refund(self):
        for rec in self:
            rec.state = 'approved'
            rec.activity_unlink(['mail.mail_activity_data_todo'])

    @api.constrains('amount', 'state')
    def _check_balance(self):
        for rec in self:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            if rec.state in ('approved', 'refund_requested') and rec.amount > (
                    active_clinic.op_fund_balance + rec.amount):
                raise ValidationError(_("Cannot approve. This disbursement exceeds the available clinic balance."))

    def unlink(self):
        for rec in self:
            raise ValidationError(
                _("Auditing Security: Disbursement vouchers cannot be deleted once created. If a mistake was made, please use the 'Reject' or 'Reset to Draft' workflow instead."))
        return super().unlink()