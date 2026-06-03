import logging
import mimetypes
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta

try:
    import boto3
except ImportError:
    boto3 = None

_logger = logging.getLogger(__name__)

S3_BUCKET_NAME = 'researchayu-operational-funds-filestore'


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

    op_fund_approval_threshold = fields.Float(string='Auto-Approval Threshold', default=0.0)

    op_fund_alert_threshold = fields.Float(string='Low Balance Alert Threshold', default=0.0)
    is_low_balance_alert_sent = fields.Boolean(string='Alert Sent Flag', default=False)

    op_fund_manager_ids = fields.Many2many('res.users', 'clinic_user_mgr_rel', 'clinic_id', 'user_id',
                                           string='Standard Approving Managers')
    op_fund_ho_manager_ids = fields.Many2many('res.users', 'clinic_user_ho_mgr_rel', 'clinic_id', 'user_id',
                                              string='Head Office Managers')
    op_fund_finance_ids = fields.Many2many('res.users', 'clinic_user_fin_rel', 'clinic_id', 'user_id',
                                           string='Finance Team')

    @api.constrains('master_fund_id')
    def _check_master_fund(self):
        for clinic in self:
            if clinic.master_fund_id == clinic:
                raise ValidationError(
                    _("A clinic cannot be its own Master Fund. Please leave the 'Shared Wallet' field blank for the main master clinic."))

    @api.depends('name', 'master_fund_id.name')
    def _compute_wallet_group(self):
        for clinic in self:
            clinic.wallet_group_name = clinic.master_fund_id.name if clinic.master_fund_id else clinic.name

    @api.depends('allocation_ids.amount', 'disbursement_ids.amount', 'disbursement_ids.state',
                 'child_clinic_ids.disbursement_ids.amount', 'child_clinic_ids.disbursement_ids.state',
                 'master_fund_id')
    def _compute_balances(self):
        for clinic in self:
            if clinic.master_fund_id and clinic.master_fund_id != clinic:
                clinic.total_allocated = 0.0
                clinic.total_spent = 0.0
                clinic.op_fund_balance = 0.0
                continue

            total_alloc = sum(clinic.allocation_ids.mapped('amount'))

            all_disbursements = clinic.disbursement_ids | clinic.child_clinic_ids.mapped('disbursement_ids')
            approved_disbs = all_disbursements.filtered(lambda d: d.state in ('approved', 'refund_requested'))
            total_spent = sum(approved_disbs.mapped('amount'))

            clinic.total_allocated = total_alloc
            clinic.total_spent = total_spent
            clinic.op_fund_balance = total_alloc - total_spent

    def _check_low_balance_alert(self):
        for clinic in self:
            if clinic.op_fund_alert_threshold > 0:
                if clinic.op_fund_balance <= clinic.op_fund_alert_threshold and not clinic.is_low_balance_alert_sent:
                    clinic._send_low_balance_notification()
                    clinic.is_low_balance_alert_sent = True
                elif clinic.op_fund_balance > clinic.op_fund_alert_threshold and clinic.is_low_balance_alert_sent:
                    clinic.is_low_balance_alert_sent = False

    def _send_low_balance_notification(self):
        for clinic in self:
            target_users = clinic.op_fund_manager_ids | clinic.op_fund_ho_manager_ids | clinic.op_fund_finance_ids
            if not target_users:
                continue

            subject = f"⚠️ URGENT: Low Balance Alert for {clinic.name}"
            body = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
                    <h2 style="color: #d9534f;">Operational Fund Low Balance Warning</h2>
                    <p style="color: #555; font-size: 16px;">The operational fund balance for <strong>{clinic.name}</strong> has dropped below the minimum safety threshold.</p>
                    <table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Current Balance:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹{clinic.op_fund_balance}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Alert Threshold:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; font-weight: bold;">₹{clinic.op_fund_alert_threshold}</td></tr>
                    </table>
                    <div style="background-color: #fcf8e3; color: #8a6d3b; padding: 15px; border-radius: 4px; border: 1px solid #faebcc;">
                        <strong>Action Required:</strong> Please arrange for a wallet top-up as soon as possible to avoid disruption of clinic operations.
                    </div>
                </div>
            """

            emails = [u.email for u in target_users if u.email]
            if emails:
                mail_values = {
                    'subject': subject,
                    'email_to': ','.join(emails),
                    'body_html': body,
                    'state': 'outgoing',
                }
                self.env['mail.mail'].sudo().create(mail_values)


class ResUsers(models.Model):
    _inherit = 'res.users'
    op_fund_managed_clinic_ids = fields.Many2many('clinic.clinic', 'clinic_user_mgr_rel', 'user_id', 'clinic_id',
                                                  string='Standard Managed Clinics')
    op_fund_ho_managed_clinic_ids = fields.Many2many('clinic.clinic', 'clinic_user_ho_mgr_rel', 'user_id', 'clinic_id',
                                                     string='HO Managed Clinics')
    op_fund_finance_clinic_ids = fields.Many2many('clinic.clinic', 'clinic_user_fin_rel', 'user_id', 'clinic_id',
                                                  string='Finance Managed Clinics')


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


class OperationalFundAllocation(models.Model):
    _name = 'operational.fund.allocation'
    _description = 'Operational Fund Top-up'
    _inherit = ['mail.thread']

    name = fields.Char(string='Receipt Number', default='New', readonly=True)
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, tracking=True,
                                default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user,
                                                                                           'clinic_id') else False)
    amount = fields.Float(string='Amount Deposited', required=True, tracking=True)
    date = fields.Date(string='Deposit Date', default=fields.Date.context_today, required=True, tracking=True)
    notes = fields.Text(string='Recharge Notes / Purpose', tracking=True)
    controller_id = fields.Many2one('res.users', string='Allocated By', default=lambda self: self.env.user,
                                    readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('operational.fund.allocation') or 'New'
        records = super().create(vals_list)
        for rec in records:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id,
                'date': rec.date,
                'transaction_type': 'credit',
                'amount': rec.amount,
                'reference': f'Wallet Top-up: {rec.name}',
                'user_id': self.env.user.id
            })
            active_clinic.sudo()._check_low_balance_alert()
        return records


class OperationalFundRejectionWizard(models.TransientModel):
    _name = 'operational.fund.rejection.wizard'
    _description = 'Disbursement Rejection Wizard'

    disbursement_id = fields.Many2one('operational.fund.disbursement', string='Disbursement', required=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_confirm_reject(self):
        for wiz in self:
            disb = wiz.disbursement_id
            if disb.state in ('approved', 'refund_requested'):
                active_clinic = disb.clinic_id.master_fund_id or disb.clinic_id
                self.env['operational.fund.audit'].sudo().create({
                    'clinic_id': active_clinic.id,
                    'date': fields.Date.context_today(self),
                    'transaction_type': 'credit',
                    'amount': disb.amount,
                    'reference': f'Reversal: Manager Overrode & Rejected Voucher {disb.name}',
                    'user_id': self.env.user.id
                })

            disb.message_post(
                body=f"<div style='color: #d9534f; font-size: 14px;'><i class='fa fa-ban'></i> <strong>VOUCHER REJECTED</strong><br/><strong>Reason:</strong> {wiz.reason}</div>",
                subtype_xmlid='mail.mt_note'
            )
            disb.state = 'rejected'
            disb.activity_unlink(['mail.mail_activity_data_todo'])
            disb._cleanup_todo_tasks('Approve Voucher')
            disb._cleanup_todo_tasks('Review Refund')


class OperationalFundDisbursement(models.Model):
    _name = 'operational.fund.disbursement'
    _description = 'Operational Fund Disbursement'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Voucher Number', default='New', readonly=True)

    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, tracking=True,
                                default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user,
                                                                                           'clinic_id') else False)
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True, tracking=True)

    payee_type = fields.Selection([('internal', 'Internal Employee'), ('external', 'External Vendor')],
                                  string='Payee Type', default='internal', tracking=True)
    payee_id = fields.Many2one('hr.employee', string='Internal Employee', tracking=True, ondelete='restrict')
    vendor_name = fields.Char(string='External Vendor Name', tracking=True)

    therapist_name = fields.Char(string='Therapist Name', tracking=True)
    payee_display = fields.Char(string='Payee', compute='_compute_payee_display', store=True)

    amount = fields.Float(string='Amount', required=True, tracking=True)

    # 🚨 UPDATED CATEGORIES: Added Clinic to Clinic, Removed Cake/Decorations 🚨
    category = fields.Selection([
        ('therapist_incentive', 'Therapist Incentive'), ('therapist_overtime', 'Therapist Overtime'),
        ('home_visit_travel', 'Home Visit Travelling'), ('fixed_therapist_travel', 'Fixed Therapist Travelling'),
        ('floater_travel', 'Floater Travelling'), ('clinic_to_clinic', 'Clinic to Clinic Travelling'),
        ('electricity', 'Electricity Bill'), ('water', 'Water Supply'), ('internet', 'Internet / Phone'),
        ('rent', 'Rent'), ('electrician', 'Electrician Charges'), ('plumber', 'Plumber Charges'),
        ('carpenter', 'Carpenter Charges'),
        ('stationary', 'Stationary'), ('printer_ink', 'Printer Ink'), ('cleaning_materials', 'Cleaning Materials'),
        ('biowaste_bags', 'Biowaste Bags'), ('other', 'Other Expense')
    ], string='Category', required=True, tracking=True)

    home_visit_mrn_search = fields.Char(string='Patient MRN Search', tracking=True)
    home_visit_patient_name = fields.Char(string='Patient Name', readonly=True)
    home_visit_patient_phone = fields.Char(string='Patient Phone', readonly=True)
    home_visit_patient_clinic = fields.Char(string='Registered Clinic', readonly=True)
    is_cross_cluster_visit = fields.Boolean(string='Is Cross-Cluster Visit', readonly=True, store=True)

    # 🚨 NEW: Clinic to Clinic Travel Details 🚨
    from_clinic_id = fields.Many2one('clinic.clinic', string='From Clinic', tracking=True)
    to_clinic_id = fields.Many2one('clinic.clinic', string='To Clinic', tracking=True)
    therapist_type = fields.Selection([('fixed', 'Fixed Therapist'), ('floater', 'Floater')], string='Therapist Role', tracking=True)

    other_expense_details = fields.Char(string='Specify Other Expense', tracking=True)
    description = fields.Text(string='Business Purpose')

    receipt_file = fields.Binary(string='Receipt Attachment')
    receipt_filename = fields.Char(string='Receipt Filename')
    is_receipt_mandatory = fields.Boolean(compute='_compute_is_receipt_mandatory')
    is_receipt_image = fields.Boolean(compute='_compute_is_receipt_image', store=True)
    is_signed_voucher_image = fields.Boolean(compute='_compute_is_signed_voucher_image', store=True)

    signed_voucher_file = fields.Binary(string='Signed Voucher (Upload)')
    signed_voucher_filename = fields.Char(string='Signed Voucher Filename')
    old_signed_voucher_file = fields.Binary(string='Original Signed Voucher (Archived)', readonly=True)
    old_signed_voucher_filename = fields.Char(string='Original Signed Voucher Filename')

    state = fields.Selection([
        ('draft', 'Draft'), ('waiting', 'Waiting Approval'), ('approved', 'Approved'),
        ('rejected', 'Rejected'), ('refund_requested', 'Refund Requested'), ('refunded', 'Refunded'),
    ], string='Status', default='draft', tracking=True)

    @api.depends('category')
    def _compute_is_receipt_mandatory(self):
        receipt_required_categories = [
            'electricity', 'water', 'internet', 'rent', 'electrician', 'plumber',
            'carpenter', 'stationary', 'printer_ink', 'cleaning_materials',
            'biowaste_bags', 'other'
        ]
        for rec in self:
            rec.is_receipt_mandatory = rec.category in receipt_required_categories

    @api.depends('receipt_filename')
    def _compute_is_receipt_image(self):
        for rec in self:
            if rec.receipt_filename:
                ext = rec.receipt_filename.split('.')[-1].lower()
                rec.is_receipt_image = ext in ['jpg', 'jpeg', 'png', 'webp']
            else:
                rec.is_receipt_image = False

    @api.depends('signed_voucher_filename')
    def _compute_is_signed_voucher_image(self):
        for rec in self:
            if rec.signed_voucher_filename:
                ext = rec.signed_voucher_filename.split('.')[-1].lower()
                rec.is_signed_voucher_image = ext in ['jpg', 'jpeg', 'png', 'webp']
            else:
                rec.is_signed_voucher_image = False

    @api.onchange('home_visit_mrn_search', 'clinic_id', 'category')
    def _onchange_home_visit_mrn(self):
        if self.home_visit_mrn_search and self.category == 'home_visit_travel':
            patient = self.env['clinic.patient'].sudo().search([('mrn', '=', self.home_visit_mrn_search)], limit=1)
            if patient:
                self.home_visit_patient_name = patient.name
                self.home_visit_patient_phone = patient.phone
                self.home_visit_patient_clinic = patient.clinic_id.name if patient.clinic_id else 'Unknown Clinic'
                active_voucher_cluster = self.clinic_id.master_fund_id or self.clinic_id
                if patient.clinic_id:
                    patient_cluster = patient.clinic_id.master_fund_id or patient.clinic_id
                    self.is_cross_cluster_visit = (active_voucher_cluster != patient_cluster)
                else:
                    self.is_cross_cluster_visit = True
            else:
                self.home_visit_patient_name, self.home_visit_patient_phone, self.home_visit_patient_clinic, self.is_cross_cluster_visit = False, False, False, False
                return {'warning': {'title': "Patient Not Found",
                                    'message': f"No patient found globally with MRN: {self.home_visit_mrn_search}"}}
        elif self.category != 'home_visit_travel':
            self.home_visit_mrn_search, self.home_visit_patient_name, self.home_visit_patient_phone, self.home_visit_patient_clinic, self.is_cross_cluster_visit = False, False, False, False, False

    @api.depends('payee_type', 'payee_id', 'vendor_name', 'category', 'therapist_name')
    def _compute_payee_display(self):
        travel_cats = ['home_visit_travel', 'fixed_therapist_travel', 'floater_travel', 'clinic_to_clinic']
        for rec in self:
            if rec.category in travel_cats and rec.therapist_name:
                rec.payee_display = rec.therapist_name
            elif rec.category in travel_cats and not rec.therapist_name:
                rec.payee_display = 'Unknown Therapist'
            elif rec.payee_type == 'internal' and rec.payee_id:
                rec.payee_display = rec.payee_id.name
            elif rec.payee_type == 'external' and rec.vendor_name:
                rec.payee_display = rec.vendor_name
            else:
                rec.payee_display = 'Unknown'

    @api.constrains('payee_type', 'payee_id', 'vendor_name', 'category', 'therapist_name')
    def _check_payee(self):
        travel_cats = ['home_visit_travel', 'fixed_therapist_travel', 'floater_travel', 'clinic_to_clinic']
        for rec in self:
            if rec.category in travel_cats:
                if not rec.therapist_name:
                    raise ValidationError(_("Please specify the Therapist Name for this travelling expense."))
            else:
                if rec.payee_type == 'internal' and not rec.payee_id:
                    raise ValidationError(_("Please select an Internal Employee."))
                if rec.payee_type == 'external' and not rec.vendor_name:
                    raise ValidationError(_("Please specify the External Vendor name."))

    @api.onchange('amount', 'clinic_id')
    def _onchange_budget_warning(self):
        if self.clinic_id and self.amount > 0:
            active_clinic = self.clinic_id.master_fund_id or self.clinic_id
            available_balance = active_clinic.op_fund_balance
            if self.amount > available_balance:
                return {'warning': {'title': "Insufficient Funds!",
                                    'message': f"This request (₹{self.amount}) exceeds the available balance (₹{available_balance}) for {active_clinic.name}."}}

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('operational.fund.disbursement') or 'New'
        return super().create(vals_list)

    def action_print_voucher(self):
        report = self.env['ir.actions.report'].search(
            [('report_name', '=', 'operational_fund.report_voucher_template')], limit=1)
        if report: return report.report_action(self)
        return False

    def action_submit_for_approval(self):
        escalated_categories = ['therapist_incentive', 'therapist_overtime', 'home_visit_travel',
                                'fixed_therapist_travel', 'floater_travel', 'clinic_to_clinic']

        for rec in self:
            if rec.amount <= 0: raise ValidationError(_("Disbursement amount must be strictly positive."))
            if not rec.signed_voucher_file: raise ValidationError(
                _("Hold on! You must download, sign, and upload the Disbursement Voucher before you can submit it."))

            if rec.is_receipt_mandatory and not rec.receipt_file:
                raise ValidationError(
                    _("Strict Auditing Rule: You must upload the original vendor receipt/bill for this expense category before submitting!"))

            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            threshold = active_clinic.op_fund_approval_threshold
            is_escalated = rec.category in escalated_categories

            if is_escalated:
                can_auto_approve = False
                target_managers = active_clinic.op_fund_ho_manager_ids
                if not target_managers: raise ValidationError(
                    _("This category requires Head Office approval, but no Head Office Managers are assigned to this clinic!"))
            else:
                can_auto_approve = (threshold > 0 and rec.amount <= threshold)
                target_managers = active_clinic.op_fund_manager_ids

            if can_auto_approve:
                if rec.amount > active_clinic.op_fund_balance: raise ValidationError(
                    _("Cannot auto-approve. Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % active_clinic.op_fund_balance)
                rec.action_approve()
                rec.message_post(
                    body=f"System Auto-Approved: The requested amount (₹{rec.amount}) is within the safe threshold of ₹{threshold}.",
                    subtype_xmlid='mail.mt_note', author_id=self.env.ref('base.partner_root').id)
            else:
                rec.state = 'waiting'
                if target_managers:
                    base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                    deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
                    deadline = fields.Date.context_today(self) + timedelta(days=1)
                    task_summary = '🏢 Head Office: Review Voucher' if is_escalated else 'Review Urgent Voucher'
                    cross_cluster_warning = f'<p style="color: #d9534f; font-weight: bold;">⚠️ Cross-Cluster Alert: Patient is registered at {rec.home_visit_patient_clinic}.</p>' if rec.is_cross_cluster_visit else ''

                    for manager in target_managers:
                        rec.activity_schedule('mail.mail_activity_data_todo', user_id=manager.id, summary=task_summary,
                                              note=f'Voucher {rec.name} for ₹{rec.amount} requires your approval. <a href="{deep_link}">Click here to view</a>')
                        if 'project.task' in self.env:
                            self.env['project.task'].sudo().create({
                                'name': f'Approve Voucher {rec.name}', 'user_ids': [(4, manager.id)],
                                'date_deadline': deadline, 'is_voucher_task': True,
                                'description': f'<p>Voucher <strong>{rec.name}</strong> for ₹{rec.amount} has been submitted by {active_clinic.name}.</p>{cross_cluster_warning}<div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-primary" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; margin-top: 10px;">Click Here to Review &amp; Action</a></div>',
                            })
                        if manager.email:
                            category_label = dict(self._fields['category'].selection).get(rec.category)
                            mail_values = {
                                'subject': f'Action Required: Approve Voucher {rec.name}', 'email_to': manager.email,
                                'body_html': f"""<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;"><h2 style="color: #333;">Voucher Approval Required</h2><p style="color: #555; font-size: 16px;">Hello {manager.name},</p><p style="color: #555; font-size: 16px;">A new operational fund disbursement requires your immediate review.</p>{cross_cluster_warning}<table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;"><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Voucher:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{rec.name}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Clinic:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{active_clinic.name}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Category:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{category_label}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Amount:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹{rec.amount}</td></tr></table><div style="text-align: center; margin-top: 30px;"><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold; display: inline-block;">Review &amp; Action Voucher</a></div></div>""",
                            }
                            self.env['mail.mail'].sudo().create(mail_values)

    def _cleanup_todo_tasks(self, task_name_prefix):
        if 'project.task' in self.env:
            for rec in self:
                tasks = self.env['project.task'].sudo().search([('name', '=', f'{task_name_prefix} {rec.name}')])
                if tasks: tasks.write({'active': False})

    def action_approve(self):
        for rec in self:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            if rec.amount > active_clinic.op_fund_balance: raise ValidationError(
                _("Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % active_clinic.op_fund_balance)
            rec.state = 'approved'
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id, 'date': rec.date, 'transaction_type': 'debit', 'amount': rec.amount,
                'reference': f'Disbursement: {rec.name} - {dict(self._fields["category"].selection).get(rec.category)}',
                'user_id': self.env.user.id
            })
            rec.activity_unlink(['mail.mail_activity_data_todo'])
            self._cleanup_todo_tasks('Approve Voucher')

            active_clinic.sudo()._check_low_balance_alert()

    def action_reject(self):
        self.ensure_one()
        return {
            'name': _('Reject Disbursement Voucher'),
            'type': 'ir.actions.act_window',
            'res_model': 'operational.fund.rejection.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_disbursement_id': self.id}
        }

    def action_delete_draft(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError(_("Only draft vouchers can be deleted."))
        self.unlink()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Disbursements',
            'res_model': 'operational.fund.disbursement',
            'view_mode': 'kanban,tree,form',
            'target': 'current'
        }

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == 'approved':
                active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
                self.env['operational.fund.audit'].sudo().create({
                    'clinic_id': active_clinic.id, 'date': fields.Date.context_today(self),
                    'transaction_type': 'credit', 'amount': rec.amount,
                    'reference': f'Reversal: Reset Approved Voucher {rec.name} to Draft', 'user_id': self.env.user.id
                })
                rec.old_signed_voucher_file, rec.old_signed_voucher_filename = rec.signed_voucher_file, rec.signed_voucher_filename
                rec.signed_voucher_file, rec.signed_voucher_filename = False, False
            elif rec.state == 'rejected':
                rec.signed_voucher_file, rec.signed_voucher_filename = False, False
                rec.old_signed_voucher_file, rec.old_signed_voucher_filename = False, False
            rec.state = 'draft'

    def action_request_refund(self):
        for rec in self:
            if rec.state != 'approved': raise ValidationError(
                _("Only fully approved disbursements can be submitted for a refund."))
            rec.state = 'refund_requested'
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            if active_clinic.op_fund_manager_ids:
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
                deadline = fields.Date.context_today(self) + timedelta(days=1)
                for manager in active_clinic.op_fund_manager_ids:
                    rec.activity_schedule('mail.mail_activity_data_todo', user_id=manager.id,
                                          summary='Review Refund Request',
                                          note=f'A refund has been requested for Voucher {rec.name}.')
                    if 'project.task' in self.env:
                        self.env['project.task'].sudo().create({
                            'name': f'Review Refund {rec.name}', 'user_ids': [(4, manager.id)],
                            'date_deadline': deadline, 'is_voucher_task': True,
                            'description': f'<p>A refund request for Voucher <strong>{rec.name}</strong> (₹{rec.amount}) requires your review.</p><br/><div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-warning" style="background-color: #f0ad4e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; margin-top: 10px;">Click Here to Action Refund</a></div>',
                        })

    def action_approve_refund(self):
        for rec in self:
            if rec.state != 'refund_requested': raise ValidationError(_("Refund must be requested first."))
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id, 'date': fields.Date.context_today(self), 'transaction_type': 'credit',
                'amount': rec.amount,
                'reference': f'Refund: Fully Reclaimed Voucher {rec.name}', 'user_id': self.env.user.id
            })
            rec.state = 'refunded'
            rec.activity_unlink(['mail.mail_activity_data_todo'])
            self._cleanup_todo_tasks('Review Refund')
            active_clinic.sudo()._check_low_balance_alert()

    def action_cancel_refund(self):
        for rec in self:
            rec.state = 'approved'
            rec.activity_unlink(['mail.mail_activity_data_todo'])
            self._cleanup_todo_tasks('Review Refund')

    def action_sync_pending_alerts(self):
        escalated_categories = ['therapist_incentive', 'therapist_overtime', 'home_visit_travel',
                                'fixed_therapist_travel', 'floater_travel', 'clinic_to_clinic']
        for rec in self:
            if rec.state != 'waiting': continue
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            is_escalated = rec.category in escalated_categories
            target_managers = active_clinic.op_fund_ho_manager_ids if is_escalated else active_clinic.op_fund_manager_ids

            if target_managers:
                base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
                deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
                deadline = fields.Date.context_today(self) + timedelta(days=1)
                task_summary = '🏢 Head Office: Review Voucher (Synced)' if is_escalated else 'Review Urgent Voucher (Synced)'
                cross_cluster_warning = f'<p style="color: #d9534f; font-weight: bold;">⚠️ Cross-Cluster Alert: Patient is registered at {rec.home_visit_patient_clinic}.</p>' if rec.is_cross_cluster_visit else ''

                for manager in target_managers:
                    task_exists = False
                    if 'project.task' in self.env:
                        task_exists = self.env['project.task'].sudo().search_count(
                            [('name', '=', f'Approve Voucher {rec.name}'), ('user_ids', 'in', manager.id),
                             ('active', '=', True)]) > 0
                    if not task_exists:
                        rec.activity_schedule('mail.mail_activity_data_todo', user_id=manager.id, summary=task_summary,
                                              note=f'Voucher {rec.name} requires your approval. <a href="{deep_link}">Click here to view</a>')
                        if 'project.task' in self.env:
                            self.env['project.task'].sudo().create({
                                'name': f'Approve Voucher {rec.name}', 'user_ids': [(4, manager.id)],
                                'date_deadline': deadline, 'is_voucher_task': True,
                                'description': f'<p>Voucher <strong>{rec.name}</strong> for ₹{rec.amount} has been submitted by {active_clinic.name}.</p>{cross_cluster_warning}<div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-primary" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; margin-top: 10px;">Click Here to Review &amp; Action</a></div>',
                            })
                        if manager.email:
                            category_label = dict(self._fields['category'].selection).get(rec.category)
                            mail_values = {
                                'subject': f'Action Required: Approve Voucher {rec.name} (Synced)',
                                'email_to': manager.email,
                                'body_html': f"""<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;"><h2 style="color: #333;">Voucher Approval Required</h2><p style="color: #555; font-size: 16px;">Hello {manager.name},</p><p style="color: #555; font-size: 16px;">This is a synced notification for a pending operational fund disbursement.</p>{cross_cluster_warning}<table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;"><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Voucher:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{rec.name}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Clinic:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{active_clinic.name}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Amount:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹{rec.amount}</td></tr></table><div style="text-align: center; margin-top: 30px;"><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold; display: inline-block;">Review &amp; Action Voucher</a></div></div>""",
                            }
                            self.env['mail.mail'].sudo().create(mail_values)

        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Sync Complete'),
                                                                                       'message': _(
                                                                                           'Approval tasks and emails have been retroactively dispatched.'),
                                                                                       'sticky': False,
                                                                                       'type': 'success'}}

    @api.constrains('amount', 'state')
    def _check_balance(self):
        for rec in self:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            if rec.state in ('approved', 'refund_requested') and rec.amount > (
                    active_clinic.op_fund_balance + rec.amount): raise ValidationError(
                _("Cannot approve. This disbursement exceeds the available clinic balance."))

    def unlink(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError(
                    _("Auditing Security: You can only delete disbursement vouchers that are still in the 'Draft' state. For all other states, please use the 'Reject' workflow."))
        return super().unlink()


class ProjectTask(models.Model):
    _inherit = 'project.task'
    is_voucher_task = fields.Boolean(string="Is Voucher Task", default=False, readonly=True)

    def unlink(self):
        for task in self:
            if task.is_voucher_task or (task.name and ('Approve Voucher' in task.name or 'Review Refund' in task.name)):
                if not self.env.su: raise ValidationError(
                    _("Auditing Security: You cannot manually delete an automated financial approval task."))
        return super().unlink()

    def write(self, vals):
        protected_fields = ['name', 'description', 'user_ids']
        for task in self:
            if task.is_voucher_task or (task.name and ('Approve Voucher' in task.name or 'Review Refund' in task.name)):
                if any(field in vals for field in protected_fields):
                    if not self.env.su: raise ValidationError(
                        _("Auditing Security: You cannot alter the title, description, or assignment of an automated financial approval task."))
        return super().write(vals)


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    is_s3_stored = fields.Boolean(string="Stored in AWS S3", default=False, index=True)
    s3_object_key = fields.Char(string="AWS S3 Object Key")

    def unlink(self):
        for attachment in self:
            if attachment.res_model == 'operational.fund.disbursement' and attachment.res_id:
                disb = self.env['operational.fund.disbursement'].browse(attachment.res_id)
                if disb.exists() and disb.state in ('approved', 'refund_requested', 'refunded'):
                    if not self.env.su:
                        raise ValidationError(
                            _("Auditing Security: You cannot delete attachments from a finalized operational disbursement. This record is sealed."))
        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not boto3:
            return records
        protected_models = ['operational.fund.disbursement', 'operational.fund.allocation']
        for rec in records:
            if rec.res_model in protected_models and rec.type == 'binary' and rec.raw:
                try:
                    s3_client = boto3.client('s3')
                    file_extension = mimetypes.guess_extension(rec.mimetype) or '.bin'
                    object_key = f"operational_funds/{rec.res_model}/{rec.res_id}_{rec.id}{file_extension}"
                    s3_client.put_object(Bucket=S3_BUCKET_NAME, Key=object_key, Body=rec.raw, ContentType=rec.mimetype)
                    rec.sudo().write({'is_s3_stored': True, 's3_object_key': object_key})
                    _logger.info(f"Successfully offloaded financial attachment {rec.id} to S3 bucket key: {object_key}")
                except Exception as e:
                    _logger.error(f"AWS S3 Cloud Offload critical failure for attachment {rec.id}: {str(e)}")
        return records

    @api.depends('store_fname', 'db_datas', 'file_size')
    def _compute_raw(self):
        super()._compute_raw()
        if boto3:
            s3_client = boto3.client('s3')
            for attach in self:
                if attach.is_s3_stored and attach.s3_object_key:
                    try:
                        s3_object = s3_client.get_object(Bucket=S3_BUCKET_NAME, Key=attach.s3_object_key)
                        attach.raw = s3_object['Body'].read()
                    except Exception as e:
                        _logger.error(f"Failed to pull asset from S3 key {attach.s3_object_key}: {str(e)}")