import io
import logging
import mimetypes
import zipfile
import base64
import csv
import os
import tempfile
from markupsafe import escape
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import timedelta
from odoo.tools.safe_eval import safe_eval
from odoo.tools import config

try:
    import boto3
except ImportError:
    boto3 = None

_logger = logging.getLogger(__name__)


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

    use_smart_threshold = fields.Boolean(string='Use Smart Threshold', default=False,
                                         help="Automatically updates alert floor using a 7-day rolling burn rate forecast.")
    is_low_balance = fields.Boolean(string='Is Low Balance', compute='_compute_is_low_balance', store=True)

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

    @api.depends('allocation_ids.amount', 'allocation_ids.state', 'disbursement_ids.amount', 'disbursement_ids.state',
                 'child_clinic_ids.disbursement_ids.amount', 'child_clinic_ids.disbursement_ids.state',
                 'master_fund_id')
    def _compute_balances(self):
        """🚨 LEGACY SAFEGUARD: Treats old records (False) as cleared to protect existing balances 🚨"""
        # Batch SQL optimization: fetch all allocation and disbursement sums in 2 read_group queries
        active_clinics = self.filtered(lambda c: not (c.master_fund_id and c.master_fund_id != c))
        all_relevant_clinics = active_clinics | active_clinics.mapped('child_clinic_ids')

        alloc_map = {}
        disb_map = {}
        if all_relevant_clinics:
            alloc_groups = self.env['operational.fund.allocation'].sudo().read_group(
                [('clinic_id', 'in', all_relevant_clinics.ids), ('state', 'in', ['cleared', False])],
                ['clinic_id', 'amount:sum'],
                ['clinic_id']
            )
            alloc_map = {g['clinic_id'][0]: g['amount'] for g in alloc_groups if g['clinic_id']}

            disb_groups = self.env['operational.fund.disbursement'].sudo().read_group(
                [('clinic_id', 'in', all_relevant_clinics.ids), ('state', 'in', ['approved', 'paid', 'refund_requested'])],
                ['clinic_id', 'amount:sum'],
                ['clinic_id']
            )
            disb_map = {g['clinic_id'][0]: g['amount'] for g in disb_groups if g['clinic_id']}

        for clinic in self:
            if clinic.master_fund_id and clinic.master_fund_id != clinic:
                clinic.total_allocated = 0.0
                clinic.total_spent = 0.0
                clinic.op_fund_balance = 0.0
                continue

            total_alloc = alloc_map.get(clinic.id, 0.0)
            total_spent = disb_map.get(clinic.id, 0.0) + sum(disb_map.get(child.id, 0.0) for child in clinic.child_clinic_ids)

            clinic.total_allocated = total_alloc
            clinic.total_spent = total_spent
            clinic.op_fund_balance = total_alloc - total_spent

    @api.depends('op_fund_balance', 'op_fund_alert_threshold')
    def _compute_is_low_balance(self):
        """
        Computes a stored boolean flag indicating if a clinic has hit its alert safety floor.
        This will drive the visual red rows/indicators on the frontend views.
        """
        for clinic in self:
            if clinic.op_fund_alert_threshold > 0:
                clinic.is_low_balance = clinic.op_fund_balance <= clinic.op_fund_alert_threshold
            else:
                clinic.is_low_balance = False

    def _check_low_balance_alert(self):
        for clinic in self:
            if clinic.op_fund_alert_threshold > 0:
                if clinic.op_fund_balance <= clinic.op_fund_alert_threshold and not clinic.is_low_balance_alert_sent:
                    clinic._send_low_balance_notification()
                    clinic.is_low_balance_alert_sent = True
                elif clinic.op_fund_balance > clinic.op_fund_alert_threshold and clinic.is_low_balance_alert_sent:
                    clinic.is_low_balance_alert_sent = False

    def _send_low_balance_notification(self):
        mail_vals_list = []
        for clinic in self:
            # Refined Audit Scope: Only alert standard managers and finance teams directly related to this clinic
            target_users = clinic.op_fund_manager_ids | clinic.op_fund_finance_ids
            if not target_users:
                continue

            subject = f"⚠️ URGENT: Low Balance Alert for {clinic.name}"
            body = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
                    <h2 style="color: #d9534f;">Operational Fund Low Balance Warning</h2>
                    <p style="color: #555; font-size: 16px;">The operational fund balance for <strong>{escape(clinic.name)}</strong> has dropped below the minimum safety threshold.</p>
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
                mail_vals_list.append({
                    'subject': subject,
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': ','.join(emails),
                    'body_html': body,
                    'state': 'outgoing',
                })
                
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    @api.model
    def _cron_calculate_smart_thresholds(self):
        """
        Option D Automation: Computes daily operational burn rate over a 30-day window
        and updates safety floors dynamically with a rolling 7-day reserve buffer.
        """
        clinics = self.search([('use_smart_threshold', '=', True)])
        date_30_days_ago = fields.Date.context_today(self) - timedelta(days=30)

        for clinic in clinics:
            # OPTIMIZED: PostgreSQL layer aggregation instead of Python memory mapping
            relevant_clinic_ids = (clinic | clinic.child_clinic_ids).ids

            disb_group = self.env['operational.fund.disbursement'].sudo().read_group(
                [
                    ('clinic_id', 'in', relevant_clinic_ids),
                    ('date', '>=', date_30_days_ago),
                    ('state', 'in', ['approved', 'paid'])
                ],
                ['amount:sum'],
                []  # No group by, we want the total sum
            )

            total_spent_30_days = disb_group[0]['amount'] if disb_group and disb_group[0]['amount'] else 0.0
            avg_daily_burn = total_spent_30_days / 30.0

            # Forecast rolling 7-day protection limit
            clinic.op_fund_alert_threshold = round(avg_daily_burn * 7, 2)


class ResUsers(models.Model):
    _inherit = 'res.users'
    op_fund_managed_clinic_ids = fields.Many2many('clinic.clinic', 'clinic_user_mgr_rel', 'user_id', 'clinic_id',
                                                  string='Standard Managed Clinics')
    op_fund_ho_managed_clinic_ids = fields.Many2many('clinic.clinic', 'clinic_user_ho_mgr_rel', 'user_id', 'clinic_id',
                                                     string='HO Managed Clinics')
    op_fund_finance_clinic_ids = fields.Many2many('clinic.clinic', 'clinic_user_fin_rel', 'user_id', 'clinic_id',
                                                  string='Finance Managed Clinics')

    @api.onchange('groups_id', 'clinic_ids')
    def _onchange_sync_tier1_clinics(self):
        """Live UI autofill: When Tier 1 is checked or clinics change, auto-populate Standard Managed Clinics."""
        tier1_group = self.env.ref('operational_fund.group_op_fund_custodian', raise_if_not_found=False)
        if tier1_group and tier1_group in self.groups_id and hasattr(self, 'clinic_ids') and self.clinic_ids:
            self.op_fund_managed_clinic_ids = [(6, 0, self.clinic_ids.ids)]

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._sync_tier1_clinics()
        return users

    def write(self, vals):
        res = super().write(vals)
        if 'groups_id' in vals or 'clinic_ids' in vals:
            self._sync_tier1_clinics()
        return res

    def _sync_tier1_clinics(self):
        """Backend Safeguard: Ensures any Tier 1 user has their clinic_ids synced to op_fund_managed_clinic_ids."""
        tier1_group = self.env.ref('operational_fund.group_op_fund_custodian', raise_if_not_found=False)
        if not tier1_group:
            return
        for user in self:
            if hasattr(user, 'clinic_ids') and tier1_group in user.groups_id and user.clinic_ids:
                if set(user.op_fund_managed_clinic_ids.ids) != set(user.clinic_ids.ids):
                    user.op_fund_managed_clinic_ids = [(6, 0, user.clinic_ids.ids)]

    def _auto_init(self):
        """Auto-sync existing accounts right during module upgrade/installation!"""
        res = super()._auto_init()
        try:
            tier1_group = self.env.ref('operational_fund.group_op_fund_custodian', raise_if_not_found=False)
            if tier1_group and tier1_group.users:
                for user in tier1_group.users:
                    if hasattr(user, 'clinic_ids') and user.clinic_ids:
                        if set(user.op_fund_managed_clinic_ids.ids) != set(user.clinic_ids.ids):
                            user.op_fund_managed_clinic_ids = [(6, 0, user.clinic_ids.ids)]
        except Exception:
            pass  # Guard during initial db table setup or early registry boot
        return res


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

    # 🚨 ADDON: The Passbook Snapshot Fields
    opening_balance = fields.Float(string='Opening Balance', readonly=True)
    amount = fields.Float(string='Amount', required=True, readonly=True)
    closing_balance = fields.Float(string='Closing Balance', readonly=True)

    reference = fields.Char(string='Reference', readonly=True)
    user_id = fields.Many2one('res.users', string='Logged By', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        """  ADDON: Automatically calculates Passbook snapshots for any new ledger entry!
        This cleanly ignores old records (so they don't break) and auto-magics the new ones.
        FIX: Resolves race conditions by fetching the last actual ledger entry's closing balance
         instead of relying on potentially stale computed balances."""

        # OPTIMIZED: Prevent PostgreSQL Deadlocks by locking all required clinics globally,
        # ordered by ID, before starting the individual transaction loop.
        clinic_ids = list(set([vals.get('clinic_id') for vals in vals_list if vals.get('clinic_id')]))
        if clinic_ids:
            self.env.cr.execute("SELECT id FROM clinic_clinic WHERE id IN %s ORDER BY id FOR UPDATE",
                                [tuple(clinic_ids)])

        # Track running balances during this transaction to support multi-create correctly
        running_balances = {}

        for vals in vals_list:
            clinic_id = vals.get('clinic_id')
            if clinic_id and 'amount' in vals and 'transaction_type' in vals:
                if clinic_id not in running_balances:
                    # Fetch the very last ledger entry for this clinic to get the exact closing balance
                    last_entry = self.search([
                        ('clinic_id', '=', clinic_id)
                    ], order='id desc', limit=1)
                    
                    running_balances[clinic_id] = last_entry.closing_balance if last_entry else 0.0

                # Snapshot the balance before the transaction applies
                opening = running_balances[clinic_id]
                vals['opening_balance'] = opening

                # Calculate the exact closing balance
                if vals['transaction_type'] == 'credit':
                    vals['closing_balance'] = opening + vals.get('amount', 0.0)
                else:
                    vals['closing_balance'] = opening - vals.get('amount', 0.0)
                    
                # Update our running tracker for the next iteration in case of multiple deposits
                running_balances[clinic_id] = vals['closing_balance']

        return super().create(vals_list)


class OperationalFundAllocation(models.Model):
    _name = 'operational.fund.allocation'
    _description = 'Operational Fund Top-up'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Receipt Number', default='New', readonly=True)
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, tracking=True, index=True,
                                default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user,
                                                                                           'clinic_id') else False)
    amount = fields.Float(string='Amount Deposited', required=True, tracking=True)
    date = fields.Date(string='Deposit Date', default=fields.Date.context_today, required=True, tracking=True, index=True)
    notes = fields.Text(string='Recharge Notes / Purpose', tracking=True)
    controller_id = fields.Many2one('res.users', string='Allocated By', default=lambda self: self.env.user,
                                    readonly=True)
    
    allocated_to_id = fields.Many2one('res.users', string='Allocated To', tracking=True, help="Specific user responsible for this deposit. They will be notified instantly.")

    # 🚨 ADDON: Introduced the 'review' Maker-Checker state
    state = fields.Selection([
        ('pending', 'Pending Acknowledgment'),
        ('review', 'Under Manager Review'),
        ('cleared', 'Cleared')
    ], string='Status', default='pending', required=True, tracking=True, index=True)

    ack_proof_file = fields.Binary(string='Bank Statement/Proof Asset')
    ack_proof_filename = fields.Char(string='Proof Filename')

    # 🚨 ADDON: File Type Detection for Live Preview
    is_ack_proof_image = fields.Boolean(compute='_compute_ack_proof_type')
    is_ack_proof_pdf = fields.Boolean(compute='_compute_ack_proof_type')

    @api.depends('ack_proof_filename')
    def _compute_ack_proof_type(self):
        """Checks the file extension to tell the XML which preview widget to render."""
        for rec in self:
            rec.is_ack_proof_image = False
            rec.is_ack_proof_pdf = False
            if rec.ack_proof_filename:
                ext = rec.ack_proof_filename.lower().split('.')[-1] if '.' in rec.ack_proof_filename else ''
                if ext in ['jpg', 'jpeg', 'png', 'webp']:
                    rec.is_ack_proof_image = True
                elif ext == 'pdf':
                    rec.is_ack_proof_pdf = True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('operational.fund.allocation') or 'New'
        records = super().create(vals_list)

        # 🚨 ADDON: Automatically trigger Maker notifications on creation
        for rec in records:
            if rec.state == 'pending':
                rec._notify_custodians_pending()
                if rec.allocated_to_id:
                    rec._notify_allocated_user()
        return records

    def _notify_allocated_user(self):
        for rec in self:
            user = rec.allocated_to_id
            if not user or not user.email: continue
            base_url = self.get_base_url()
            deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.allocation&view_type=form"
            mail_values = {
                'subject': f'Direct Allocation: Pending HQ Deposit for {rec.clinic_id.name}',
                'email_from': '<noreply@researchayu.com>',
                'email_to': user.email,
                'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #333;">Direct Capital Deposit</h2><p>Hello {escape(user.name)},</p><p>HQ has directly allocated <strong>₹{rec.amount}</strong> to {escape(rec.clinic_id.name)} under your name. Please log in and upload the bank verification proof to clear it.</p><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Acknowledge Funds</a></div>""",
                'state': 'outgoing',
            }
            self.env['mail.mail'].sudo().create(mail_values)

    def _notify_custodians_pending(self):
        """Finds authorized clinic custodians, assigns a Today deadline To-Do, and emails them."""
        # Batch collect all clinic IDs for efficient querying
        clinic_ids = self.mapped('clinic_id').ids
        if not clinic_ids:
            return

        custodians = self.env['res.users'].sudo().search([
            ('groups_id', 'in', self.env.ref('operational_fund.group_op_fund_custodian').id),
            '|', ('clinic_ids', 'in', clinic_ids),
            ('op_fund_managed_clinic_ids', 'in', clinic_ids)
        ])
        
        # Pre-fetch base URL
        base_url = self.get_base_url()
        deadline = fields.Date.context_today(self)
        
        # Batch email values to send emails in one go
        mail_values_list = []
        
        for rec in self:
            # Filter custodians relevant for this specific record
            rec_custodians = custodians.filtered(
                lambda u: rec.clinic_id in u.clinic_ids or rec.clinic_id in u.op_fund_managed_clinic_ids
            )
            if not rec_custodians:
                continue

            deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.allocation&view_type=form"

            for user in rec_custodians:
                rec.activity_schedule(
                    'mail.activity_data_todo',
                    user_id=user.id,
                    summary='Action Required: Acknowledge HQ Deposit',
                    note=f'A new deposit of ₹{rec.amount} requires your bank proof upload. <a href="{deep_link}">Click here to act</a>',
                    date_deadline=deadline
                )

                if user.email:
                    mail_values_list.append({
                        'subject': f'Action Required: Pending HQ Deposit for {rec.clinic_id.name}',
                        'email_from': '<noreply@researchayu.com>',
                        'email_to': user.email,
                        'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #333;">Capital Deposit Pending</h2><p>Hello {escape(user.name)},</p><p>HQ has allocated <strong>₹{rec.amount}</strong> to {escape(rec.clinic_id.name)}. Please log in and upload the bank verification proof today to unlock your dashboard.</p><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Acknowledge Funds</a></div>""",
                        'state': 'outgoing',
                    })
        
        if mail_values_list:
            self.env['mail.mail'].sudo().create(mail_values_list)

    def action_submit_for_review(self, file_data, filename):
        """🚨 ADDON: The Custodian uploads proof, pushing it to the Checker (Manager)."""
        self.ensure_one()
        if not file_data:
            raise ValidationError(_("Auditing Error: You must attach a bank proof file to submit for review."))

        self.write({
            'ack_proof_file': file_data,
            'ack_proof_filename': filename,
            'state': 'review'
        })

        # Clear Custodian's To-Do
        self.activity_unlink(['mail.activity_data_todo'])

        # Notify Managers for Review
        target_managers = self.clinic_id.op_fund_manager_ids
        deadline = fields.Date.context_today(self)
        base_url = self.get_base_url()
        deep_link = f"{base_url}/web#id={self.id}&model=operational.fund.allocation&view_type=form"

        for manager in target_managers:
            self.activity_schedule(
                'mail.activity_data_todo',
                user_id=manager.id,
                summary='Review Required: Verify Bank Proof',
                note=f'Deposit {self.name} (₹{self.amount}) has bank proof ready for your review. <a href="{deep_link}">Click here</a>',
                date_deadline=deadline
            )

    def action_approve_allocation(self):
        """🚨 ADDON: Manager approves the proof. Money is credited."""
        mail_vals_list = []
        for rec in self:
            rec.state = 'cleared'
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            # Formally inject the balance into the ledger
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id,
                'date': rec.date,
                'transaction_type': 'credit',
                'amount': rec.amount,
                'reference': f'Wallet Top-up: {rec.name} (Approved by Manager)',
                'user_id': self.env.user.id
            })

            active_clinic.sudo()._check_low_balance_alert()
            rec.activity_unlink(['mail.activity_data_todo'])
            
            # Send Notification
            target_user = rec.allocated_to_id or rec.create_uid
            if target_user and target_user.email:
                mail_vals_list.append({
                    'subject': f'Approved: Deposit for {rec.clinic_id.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': target_user.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #28a745;">Deposit Approved</h2><p>Hello,</p><p>The bank proof for your deposit of <strong>₹{rec.amount}</strong> for {escape(rec.clinic_id.name)} has been approved by the Manager.</p><p>The funds are now available in the clinic wallet.</p></div>""",
                    'state': 'outgoing',
                })
        
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    def action_reject_allocation(self):
        """🚨 ADDON: Manager rejects the proof. Sends it back to Custodian."""
        mail_vals_list = []
        for rec in self:
            rec.write({
                'ack_proof_file': False,
                'ack_proof_filename': False,
                'state': 'pending'
            })
            rec.activity_unlink(['mail.activity_data_todo'])
            rec.message_post(
                body="<div style='color:red;'><strong>REJECTED:</strong> The uploaded bank proof was rejected by the Manager. Please re-upload a valid proof document.</div>")
            rec._notify_custodians_pending()
            
            # Send Notification
            target_user = rec.allocated_to_id or rec.create_uid
            if target_user and target_user.email:
                mail_vals_list.append({
                    'subject': f'Rejected: Bank Proof for {rec.clinic_id.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': target_user.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #d9534f;">Proof Rejected</h2><p>Hello,</p><p>The bank proof for your deposit of <strong>₹{rec.amount}</strong> for {escape(rec.clinic_id.name)} was rejected by the Manager.</p><p>Please re-upload a valid proof document.</p></div>""",
                    'state': 'outgoing',
                })
        
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    @api.model
    def _cron_check_overdue_allocations(self):
        """🚨 ADDON: Cron Job checks for 24h SLA Breaches and escalates to Managers."""
        overdue_date = fields.Date.context_today(self) - timedelta(days=1)
        overdue_allocs = self.search([('state', '=', 'pending'), ('date', '<=', overdue_date)])

        mail_vals_list = []
        for alloc in overdue_allocs:
            managers = alloc.clinic_id.op_fund_manager_ids
            base_url = self.get_base_url()
            deep_link = f"{base_url}/web#id={alloc.id}&model=operational.fund.allocation&view_type=form"

            for manager in managers:
                alloc.activity_schedule(
                    'mail.activity_data_todo',
                    user_id=manager.id,
                    summary='⚠️ SLA BREACH: Pending Deposit Unacknowledged',
                    note=f'Clinic Custodian has not acknowledged Deposit {alloc.name} (₹{alloc.amount}) within 24 hours. Please follow up. <a href="{deep_link}">Click here</a>'
                )

                if manager.email:
                    mail_vals_list.append({
                        'subject': f'SLA BREACH: Overdue Acknowledgment for {alloc.clinic_id.name}',
                        'email_from': '<noreply@researchayu.com>',
                        'email_to': manager.email,
                        'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px; border: 1px solid #d9534f;"><h2 style="color: #d9534f;">⚠️ 24-Hour SLA Breach Alert</h2><p>Hello {escape(manager.name)},</p><p>The Tier 1 Custodians at <strong>{escape(alloc.clinic_id.name)}</strong> have failed to acknowledge Deposit {escape(alloc.name)} (₹{alloc.amount}) within the mandated 24-hour window.</p><p>Please intervene to ensure the funds are cleared and their dashboard is unlocked.</p><a href="{deep_link}" style="background-color: #d9534f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">View Record</a></div>""",
                        'state': 'outgoing',
                    })
                    
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)


class OperationalFundAllocationWizard(models.TransientModel):
    _name = 'operational.fund.allocation.wizard'
    _description = 'Top-up Acknowledgment Popup Wizard'

    allocation_id = fields.Many2one(
        'operational.fund.allocation', 
        string='Pending Deposit (Select Clinic)', 
        required=True,
        domain="[('state', '=', 'pending')]"
    )
    amount = fields.Float(related='allocation_id.amount', string='Amount Transferred', readonly=True)
    notes = fields.Text(related='allocation_id.notes', string='HQ Recharge Notes', readonly=True)

    ack_proof_file = fields.Binary(string='Upload Bank Snippet / Screenshot')
    ack_proof_filename = fields.Char(string='Filename')

    def action_confirm_receipt(self):
        """🚨 ADDON: Submits the proof for Manager Review instead of clearing it."""
        self.ensure_one()
        if not self.ack_proof_file:
            raise ValidationError(
                _("Auditing Restriction: You must attach an image verification snapshot of the bank statement payout rollout to acknowledge this allocation."))

        # Now routes to Maker-Checker Review instead of immediate clear
        self.allocation_id.sudo().action_submit_for_review(self.ack_proof_file, self.ack_proof_filename)

        action_ref = self.env.context.get('return_action', 'operational_fund.action_op_fund_disbursement')
        return self.env['ir.actions.act_window']._for_xml_id(action_ref)

    def action_close_and_continue(self):
        """Restored: Lets the user dismiss the pop-up and freely access their intended screen."""
        action_ref = self.env.context.get('return_action', 'operational_fund.action_op_fund_disbursement')
        return self.env['ir.actions.act_window']._for_xml_id(action_ref)


class OperationalFundRejectionWizard(models.TransientModel):
    _name = 'operational.fund.rejection.wizard'
    _description = 'Disbursement Rejection Wizard'

    disbursement_id = fields.Many2one('operational.fund.disbursement', string='Disbursement', required=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_confirm_reject(self):
        mail_vals_list = []
        for wiz in self:
            disb = wiz.disbursement_id
            if disb.state in ('approved', 'paid', 'refund_requested'):
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
            disb.activity_unlink(['mail.activity_data_todo'])
            disb._cleanup_todo_tasks('Approve Voucher')
            disb._cleanup_todo_tasks('Review Refund')
            
            if disb.create_uid and disb.create_uid.email:
                mail_vals_list.append({
                    'subject': f'Rejected: Voucher {disb.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': disb.create_uid.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #d9534f;">Voucher Rejected</h2><p>Hello,</p><p>Your voucher <strong>{escape(disb.name)}</strong> for ₹{disb.amount} was rejected.</p><p><strong>Reason:</strong> {escape(wiz.reason)}</p></div>""",
                    'state': 'outgoing',
                })
                
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)


class OperationalFundDisbursement(models.Model):
    _name = 'operational.fund.disbursement'
    _description = 'Operational Fund Disbursement'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Voucher Number', default='New', readonly=True)

    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, tracking=True, index=True,
                                default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user,
                                                                                           'clinic_id') else False)
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True, tracking=True, index=True)
    is_today = fields.Boolean(string="Is Today's Voucher", compute='_compute_is_today', search='_search_is_today')

    expense_category = fields.Selection([
        ('incentive', 'Therapist Incentive'),
        ('overtime', 'Therapist Overtime'),
        ('travel', 'Travel & Commute'),
        ('office', 'Office & Clinic Expenses'),
        ('other', 'Other Expense')
    ], string='Main Category', tracking=True, index=True)

    therapist_role = fields.Selection([
        ('home', 'Home Therapist'),
        ('fixed', 'Fixed Therapist'),
        ('floater', 'Floater Therapist')
    ], string='Therapist Role', tracking=True)

    travel_type = fields.Selection([
        ('home', 'Home Visit Travel'),
        ('fixed', 'Fixed Therapist Travel'),
        ('floater', 'Floater Travel'),
        ('c2c', 'Clinic to Clinic Travel')
    ], string='Travel Route', tracking=True)

    office_expense_type = fields.Selection([
        ('electricity', 'Electricity Bill'), ('water', 'Water Supply'), ('internet', 'Internet / Phone'),
        ('rent', 'Rent'), ('electrician', 'Electrician Charges'), ('plumber', 'Plumber Charges'),
        ('carpenter', 'Carpenter Charges'), ('stationary', 'Stationary'), ('printer_ink', 'Printer Ink'),
        ('cleaning_materials', 'Cleaning Materials'), ('biowaste_bags', 'Biowaste Bags')
    ], string='Expense Type', tracking=True)

    display_category = fields.Char(string='Category', compute='_compute_display_category', store=True)

    category = fields.Selection([
        ('therapist_incentive', 'Therapist Incentive'), ('therapist_overtime', 'Therapist Overtime'),
        ('home_visit_travel', 'Home Visit Travelling'), ('fixed_therapist_travel', 'Fixed Therapist Travelling'),
        ('floater_travel', 'Floater Travelling'), ('clinic_to_clinic', 'Clinic to Clinic Travelling'),
        ('electricity', 'Electricity Bill'), ('water', 'Water Supply'), ('internet', 'Internet / Phone'),
        ('rent', 'Rent'), ('electrician', 'Electrician Charges'), ('plumber', 'Plumber Charges'),
        ('carpenter', 'Carpenter Charges'), ('stationary', 'Stationary'), ('printer_ink', 'Printer Ink'),
        ('cleaning_materials', 'Cleaning Materials'), ('biowaste_bags', 'Biowaste Bags'),
        ('cake', 'Cake (Legacy)'), ('decorations', 'Decorations (Legacy)'), ('other', 'Other Expense')
    ], string='Legacy Category', tracking=True)

    payee_type = fields.Selection([('internal', 'Internal Employee'), ('external', 'External Vendor')],
                                  string='Legacy Payee Type', tracking=True)

    payee_id = fields.Many2one('hr.employee', string='Select Employee', tracking=True, ondelete='restrict', index=True)
    therapist_name = fields.Char(string='Therapist Name', tracking=True)
    vendor_name = fields.Char(string='Vendor / Payee Name', tracking=True)

    payee_display = fields.Char(string='Payee', compute='_compute_payee_display', store=True)
    amount = fields.Float(string='Amount', required=True, tracking=True)

    home_visit_mrn_search = fields.Char(string='Patient MRN Search', tracking=True)
    home_visit_patient_name = fields.Char(string='Patient Name', readonly=True)
    home_visit_patient_phone = fields.Char(string='Patient Phone', readonly=True)
    home_visit_patient_clinic = fields.Char(string='Registered Clinic', readonly=True)
    is_cross_cluster_visit = fields.Boolean(string='Is Cross-Cluster Visit', readonly=True, store=True)

    from_clinic_id = fields.Many2one('clinic.clinic', string='From Clinic', tracking=True)
    to_clinic_id = fields.Many2one('clinic.clinic', string='To Clinic', tracking=True)

    therapist_type = fields.Selection([('fixed', 'Fixed Therapist'), ('floater', 'Floater')], string='Therapist Role',
                                      tracking=True)

    other_expense_details = fields.Char(string='Specify Other Expense', tracking=True)
    description = fields.Text(string='Business Purpose')

    receipt_file = fields.Binary(string='Receipt Attachment')
    receipt_filename = fields.Char(string='Receipt Filename')
    is_receipt_mandatory = fields.Boolean(compute='_compute_is_receipt_mandatory')
    is_receipt_image = fields.Boolean(compute='_compute_is_receipt_image', store=True)

    signed_voucher_file = fields.Binary(string='Signed Voucher (Upload)')
    signed_voucher_filename = fields.Char(string='Signed Voucher Filename')
    is_signed_voucher_image = fields.Boolean(compute='_compute_is_signed_voucher_image', store=True)

    old_signed_voucher_file = fields.Binary(string='Original Signed Voucher (Archived)', readonly=True)
    old_signed_voucher_filename = fields.Char(string='Original Signed Voucher Filename')

    state = fields.Selection([
        ('draft', 'Draft'), ('waiting', 'Waiting Approval'), ('approved', 'Approved'),
        ('paid', 'Paid'), ('rejected', 'Rejected'), ('refund_requested', 'Refund Requested'), ('refunded', 'Refunded'),
    ], string='Status', default='draft', tracking=True, index=True)

    payment_screenshot = fields.Binary(string='Transaction Proof Screenshot')
    payment_screenshot_filename = fields.Char(string='Payment Proof Filename')
    is_payment_screenshot_image = fields.Boolean(compute='_compute_is_payment_image', store=True)

    receipt_preview_image = fields.Binary(related='receipt_file', string="Receipt Preview Image")
    receipt_preview_pdf = fields.Binary(related='receipt_file', string="Receipt Preview PDF")

    signed_voucher_preview_image = fields.Binary(related='signed_voucher_file', string="Voucher Preview Image")
    signed_voucher_preview_pdf = fields.Binary(related='signed_voucher_file', string="Voucher Preview PDF")

    payment_screenshot_preview_image = fields.Binary(related='payment_screenshot', string="Payment Preview Image")
    payment_screenshot_preview_pdf = fields.Binary(related='payment_screenshot', string="Payment Preview PDF")

    s3_receipt_url = fields.Char(string="S3 Direct Receipt Link", compute="_compute_s3_export_urls")
    s3_voucher_url = fields.Char(string="S3 Direct Voucher Link", compute="_compute_s3_export_urls")
    s3_payment_url = fields.Char(string="S3 Direct Payment Link", compute="_compute_s3_export_urls")

    show_employee_payee = fields.Boolean(compute='_compute_ui_visibility')
    show_therapist_name_input = fields.Boolean(compute='_compute_ui_visibility')
    show_vendor_payee = fields.Boolean(compute='_compute_ui_visibility')
    show_clinic_transfer = fields.Boolean(compute='_compute_ui_visibility')
    show_home_visit = fields.Boolean(compute='_compute_ui_visibility')

    show_therapist_role = fields.Boolean(compute='_compute_ui_visibility')
    show_travel_type = fields.Boolean(compute='_compute_ui_visibility')
    show_office_type = fields.Boolean(compute='_compute_ui_visibility')
    show_other_expense = fields.Boolean(compute='_compute_ui_visibility')

    has_pending_allocation = fields.Boolean(string="Has Pending Funds", compute='_compute_has_pending_allocation')

    @api.depends('date')
    def _compute_is_today(self):
        today = fields.Date.context_today(self)
        for record in self:
            record.is_today = (record.date == today)

    def _search_is_today(self, operator, value):
        today = fields.Date.context_today(self)
        if (operator == '=' and value) or (operator == '!=' and not value):
            return [('date', '=', today)]
        return [('date', '!=', today)]

    @api.depends('clinic_id')
    def _compute_has_pending_allocation(self):
        # Optimization: Fetch all pending allocations in a single query instead of a loop
        clinics = self.mapped('clinic_id')
        if clinics:
            pending_allocs = self.env['operational.fund.allocation'].sudo().read_group(
                [('clinic_id', 'in', clinics.ids), ('state', '=', 'pending')],
                ['clinic_id'],
                ['clinic_id']
            )
            pending_clinic_ids = {alloc['clinic_id'][0] for alloc in pending_allocs if alloc['clinic_id_count'] > 0}
        else:
            pending_clinic_ids = set()

        for rec in self:
            if rec.clinic_id:
                rec.has_pending_allocation = rec.clinic_id.id in pending_clinic_ids
            else:
                rec.has_pending_allocation = False

    @api.model
    def _get_user_clinic_ids(self, user=None):
        """DRY Helper: Returns list of clinic IDs assigned/managed by the given user (or current user)."""
        user = user or self.env.user
        clinic_ids = set()
        if hasattr(user, 'clinic_id') and user.clinic_id:
            clinic_ids.add(user.clinic_id.id)
        if hasattr(user, 'op_fund_managed_clinic_ids'):
            clinic_ids.update(user.op_fund_managed_clinic_ids.ids)
        if hasattr(user, 'op_fund_ho_managed_clinic_ids'):
            clinic_ids.update(user.op_fund_ho_managed_clinic_ids.ids)
        return list(clinic_ids)

    @api.model
    def action_check_pending_allocations(self):
        """Menu Interceptor: Checks for funds, opens popup if needed, otherwise opens Disbursements."""
        clinic_ids = self._get_user_clinic_ids()
        if clinic_ids:
            pending_alloc = self.env['operational.fund.allocation'].sudo().search([
                ('state', '=', 'pending'),
                ('clinic_id', 'in', clinic_ids)
            ], limit=1)
            if pending_alloc:
                return {
                    'name': _('MANDATORY ACTION: Pending Capital Deposit'),
                    'type': 'ir.actions.act_window',
                    'res_model': 'operational.fund.allocation.wizard',
                    'view_mode': 'form',
                    'target': 'current',
                    'context': {
                        'default_allocation_id': pending_alloc.id,
                        'return_action': 'operational_fund.action_op_fund_disbursement'
                    },
                    'flags': {'headless': True}
                }
        return self.env['ir.actions.act_window']._for_xml_id('operational_fund.action_op_fund_disbursement')

    @api.model
    def action_check_pending_allocations_dashboard(self):
        """Menu Interceptor: Checks for funds, opens popup if needed, otherwise opens Dashboard."""
        clinic_ids = self._get_user_clinic_ids()
        if clinic_ids:
            pending_alloc = self.env['operational.fund.allocation'].sudo().search([
                ('state', '=', 'pending'),
                ('clinic_id', 'in', clinic_ids)
            ], limit=1)
            if pending_alloc:
                return {
                    'name': _('MANDATORY ACTION: Pending Capital Deposit'),
                    'type': 'ir.actions.act_window',
                    'res_model': 'operational.fund.allocation.wizard',
                    'view_mode': 'form',
                    'target': 'current',
                    'context': {
                        'default_allocation_id': pending_alloc.id,
                        'return_action': 'operational_fund.action_op_fund_clinic_balance'
                    },
                    'flags': {'headless': True}
                }
        return self.env['ir.actions.act_window']._for_xml_id('operational_fund.action_op_fund_clinic_balance')

    def action_open_acknowledgment_wizard_from_banner(self):
        """Triggered when user clicks the red banner to acknowledge a deposit."""
        clinic_ids = self._get_user_clinic_ids()
        if clinic_ids:
            pending_alloc = self.env['operational.fund.allocation'].sudo().search([
                ('state', '=', 'pending'),
                ('clinic_id', 'in', clinic_ids)
            ], limit=1)
            if pending_alloc:
                return {
                    'name': _('MANDATORY ACTION: Pending Capital Deposit'),
                    'type': 'ir.actions.act_window',
                    'res_model': 'operational.fund.allocation.wizard',
                    'view_mode': 'form',
                    'target': 'new',
                    'context': {
                        'default_allocation_id': pending_alloc.id,
                    }
                }
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @api.depends('category', 'expense_category', 'therapist_role', 'travel_type', 'payee_type')
    def _compute_ui_visibility(self):
        for rec in self:
            emp, ther, vend, trans, home = False, False, False, False, False
            role, trav, off, oth = False, False, False, False

            if rec.category and not rec.expense_category:
                if rec.category == 'home_visit_travel':
                    home, ther = True, True
                elif rec.category == 'clinic_to_clinic':
                    trans, ther = True, True
                elif rec.category in ['fixed_therapist_travel', 'floater_travel']:
                    ther = True
                elif rec.category == 'other':
                    oth, vend = True, True
                else:
                    if rec.payee_type == 'internal':
                        emp = True
                    elif rec.payee_type == 'external':
                        vend = True
            else:
                if rec.expense_category in ['incentive', 'overtime']:
                    role = True
                    ther = True
                    if rec.therapist_role == 'home': home = True
                elif rec.expense_category == 'travel':
                    trav = True
                    if rec.travel_type in ['fixed', 'home', 'floater']:
                        ther = True
                    elif rec.travel_type == 'c2c':
                        role, trans, ther = True, True, True
                    if rec.travel_type == 'home': home = True
                elif rec.expense_category == 'office':
                    off, vend = True, True
                elif rec.expense_category == 'other':
                    oth, vend = True, True

            rec.show_employee_payee = emp
            rec.show_therapist_name_input = ther
            rec.show_vendor_payee = vend
            rec.show_clinic_transfer = trans
            rec.show_home_visit = home
            rec.show_therapist_role = role
            rec.show_travel_type = trav
            rec.show_office_type = off
            rec.show_other_expense = oth

    @api.depends('category', 'expense_category', 'therapist_role', 'travel_type', 'office_expense_type')
    def _compute_display_category(self):
        for rec in self:
            if rec.expense_category:
                if rec.expense_category == 'incentive':
                    role = dict(self._fields['therapist_role'].selection).get(rec.therapist_role, '')
                    rec.display_category = f"Incentive ({role})" if role else "Therapist Incentive"
                elif rec.expense_category == 'overtime':
                    role = dict(self._fields['therapist_role'].selection).get(rec.therapist_role, '')
                    rec.display_category = f"Overtime ({role})" if role else "Therapist Overtime"
                elif rec.expense_category == 'travel':
                    ttype = dict(self._fields['travel_type'].selection).get(rec.travel_type, '')
                    rec.display_category = f"Travel ({ttype})" if ttype else "Travel & Commute"
                elif rec.expense_category == 'office':
                    otype = dict(self._fields['office_expense_type'].selection).get(rec.office_expense_type, '')
                    rec.display_category = f"Office ({otype})" if otype else "Office Expenses"
                else:
                    rec.display_category = "Other Expense"
            else:
                rec.display_category = dict(self._fields['category'].selection).get(rec.category, 'Unknown Category')

    @api.depends('expense_category', 'category')
    def _compute_is_receipt_mandatory(self):
        legacy_receipt_required = ['electricity', 'water', 'internet', 'rent', 'electrician', 'plumber', 'carpenter',
                                   'stationary', 'printer_ink', 'cleaning_materials', 'biowaste_bags', 'other']
        for rec in self:
            if rec.expense_category:
                rec.is_receipt_mandatory = rec.expense_category in ['office', 'other']
            else:
                rec.is_receipt_mandatory = rec.category in legacy_receipt_required

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

    @api.depends('payment_screenshot_filename')
    def _compute_is_payment_image(self):
        for rec in self:
            if rec.payment_screenshot_filename:
                ext = rec.payment_screenshot_filename.split('.')[-1].lower()
                rec.is_payment_screenshot_image = ext in ['jpg', 'jpeg', 'png', 'webp']
            else:
                rec.is_payment_screenshot_image = False

    @api.depends('name')
    def _compute_s3_export_urls(self):
        for rec in self:
            rec.s3_receipt_url = False
            rec.s3_voucher_url = False
            rec.s3_payment_url = False

        if not self.ids:
            return

        s3_client, bucket = None, None
        if boto3:
            try:
                s3_client, bucket = self.env['ir.attachment']._get_s3_credentials()
            except Exception:
                pass

        if not s3_client or not bucket:
            return

        # Optimization: Fetch all related attachments in a single query
        attachments = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'operational.fund.disbursement'),
            ('res_id', 'in', self.ids),
            ('is_s3_stored', '=', True)
        ])
        
        att_map = {}
        for att in attachments:
            if att.res_id not in att_map:
                att_map[att.res_id] = []
            att_map[att.res_id].append(att)

        for rec in self:
            for att in att_map.get(rec.id, []):
                try:
                    # Generate a secure pre-signed URL valid for 7 days
                    url = s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket, 'Key': att.s3_object_key},
                        ExpiresIn=604800
                    )
                except Exception:
                    url = False
                    
                if att.res_field == 'receipt_file':
                    rec.s3_receipt_url = url
                elif att.res_field == 'signed_voucher_file':
                    rec.s3_voucher_url = url
                elif att.res_field == 'payment_screenshot':
                    rec.s3_payment_url = url

    @api.onchange('home_visit_mrn_search', 'clinic_id', 'category', 'expense_category', 'travel_type', 'therapist_role')
    def _onchange_home_visit_mrn(self):
        if self.home_visit_mrn_search and self.show_home_visit:
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
        elif not self.show_home_visit:
            self.home_visit_mrn_search, self.home_visit_patient_name, self.home_visit_patient_phone, self.home_visit_patient_clinic, self.is_cross_cluster_visit = False, False, False, False, False

    @api.depends('category', 'expense_category', 'payee_type', 'payee_id', 'vendor_name', 'therapist_name',
                 'therapist_role', 'travel_type')
    def _compute_payee_display(self):
        for rec in self:
            if rec.category and not rec.expense_category:
                if rec.category in ['home_visit_travel', 'fixed_therapist_travel', 'floater_travel',
                                    'clinic_to_clinic'] and rec.therapist_name:
                    rec.payee_display = rec.therapist_name
                elif rec.payee_type == 'internal' and rec.payee_id:
                    rec.payee_display = rec.payee_id.name
                elif rec.payee_type == 'external' and rec.vendor_name:
                    rec.payee_display = rec.vendor_name
                else:
                    rec.payee_display = 'Unknown Payee'
            else:
                if rec.expense_category in ['incentive', 'overtime', 'travel']:
                    if rec.therapist_name:
                        rec.payee_display = rec.therapist_name
                    else:
                        rec.payee_display = 'Unknown Payee'
                elif rec.expense_category in ['office', 'other'] and rec.vendor_name:
                    rec.payee_display = rec.vendor_name
                else:
                    rec.payee_display = 'Unknown Payee'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('operational.fund.disbursement') or 'New'
        return super().create(vals_list)

    def action_print_voucher(self):
        report = self.env.ref('operational_fund.action_report_op_fund_voucher', raise_if_not_found=False)
        if not report:
            report = self.env['ir.actions.report'].search([('report_name', '=', 'operational_fund.report_voucher_template')], limit=1)
        if report:
            return report.report_action(self)
        return False

    def action_submit_for_approval(self):
        # Optimization: Pre-fetch active clinics and pending allocations
        clinic_ids = (self.mapped('clinic_id') | self.mapped('clinic_id.master_fund_id')).ids
        pending_recharges = self.env['operational.fund.allocation'].sudo().search([
            ('clinic_id', 'in', clinic_ids),
            ('state', '=', 'pending')
        ])
        pending_recharge_clinics = pending_recharges.mapped('clinic_id').ids

        # Optimization: Fetch rules once
        rules = self.env['operational.fund.approval.rule'].sudo().search([('active', '=', True)], order='sequence, id')

        for rec in self:
            if rec.amount <= 0: raise ValidationError(_("Disbursement amount must be strictly positive."))
            if rec.show_employee_payee and not rec.payee_id: raise ValidationError(
                _("Missing Parameter: Please select an Employee Profile."))
            if rec.show_therapist_name_input and not rec.therapist_name: raise ValidationError(
                _("Missing Parameter: Please type the Therapist Name."))
            if rec.show_vendor_payee and not rec.vendor_name: raise ValidationError(
                _("Missing Parameter: Please specify the Vendor or Payee Name."))
            if rec.show_home_visit and not rec.home_visit_mrn_search: raise ValidationError(
                _("Missing Compliance Parameter: You must enter the patient MRN code for home visits."))
            if not rec.signed_voucher_file: raise ValidationError(
                _("Hold on! You must download, sign, and upload the physical Disbursement Voucher before you can submit it."))
            if rec.is_receipt_mandatory and not rec.receipt_file: raise ValidationError(
                _("Strict Auditing Rule: You must upload the original vendor receipt/bill for this expense category before submitting!"))

            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            if active_clinic.id in pending_recharge_clinics:
                raise ValidationError(
                    _("Access Denied: The clinic '%s' has a pending capital deposit from HQ. You must upload the bank proof and acknowledge receipt of these funds before submitting new vouchers.") % active_clinic.name)

            # Validate live balance BEFORE rules
            if rec.amount > active_clinic.op_fund_balance:
                raise ValidationError(
                    _("Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % active_clinic.op_fund_balance)

            # 🚨 THE RULES ENGINE EVALUATOR 🚨
            matched_rule = False

            for rule in rules:
                # Safely evaluate the dynamic domain string against the current voucher record
                rule_domain = safe_eval(rule.domain or '[]')
                if rec.filtered_domain(rule_domain):
                    matched_rule = rule
                    break  # Stop at the highest priority matching rule

            if not matched_rule:
                raise ValidationError(
                    _("System Error: No financial routing rule matches this voucher's criteria. Please contact an Administrator to configure an Approval Rule."))

            # Add CC Followers silently for auditing
            if matched_rule.cc_user_ids:
                rec.message_subscribe(partner_ids=matched_rule.cc_user_ids.mapped('partner_id').ids)

            # --- EXECUTE THE RULE OUTCOME ---
            if matched_rule.action_type == 'block':
                raise ValidationError(matched_rule.block_message or _(
                    "This voucher violates operational policies and has been blocked by a system rule."))

            elif matched_rule.action_type == 'auto_approve':
                rec.action_approve()
                rec.message_post(
                    body=f"<strong>System Auto-Approved:</strong> Passed via automated rule <em>'{matched_rule.name}'</em>.",
                    subtype_xmlid='mail.mt_note',
                    author_id=self.env.ref('base.partner_root').id
                )

            elif matched_rule.action_type == 'require_approval':

                # SMART FALLBACK: Use rule approvers if set, otherwise route to the clinic's standard managers

                final_approvers = matched_rule.approver_ids or active_clinic.op_fund_manager_ids

                if not final_approvers:
                    raise ValidationError(

                        _(f"Configuration Error: Rule '{matched_rule.name}' triggered, but there are no Assigned Approvers on the rule, and '{active_clinic.name}' has no Standard Managers set up."))

                rec.state = 'waiting'

                base_url = self.get_base_url()

                deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"

                deadline = fields.Date.context_today(self) + timedelta(days=1)

                cross_cluster_warning = f'<p style="color: #d9534f; font-weight: bold;">⚠️ Cross-Cluster Alert: Patient is registered at {rec.home_visit_patient_clinic}.</p>' if rec.is_cross_cluster_visit else ''

                task_vals_list = []
                mail_vals_list = []
                for manager in final_approvers:
                    rec.activity_schedule('mail.activity_data_todo', user_id=manager.id, summary='Review Voucher',
                                          note=f'Rule Triggered: {matched_rule.name}. <a href="{deep_link}">Click here to view</a>')

                    if 'project.task' in self.env:
                        task_vals_list.append({
                            'name': f'Approve Voucher {rec.name}', 'user_ids': [(4, manager.id)],
                            'date_deadline': deadline, 'is_voucher_task': True,
                            'description': f'<p>Automated Route via Rule: <strong>{matched_rule.name}</strong></p>{cross_cluster_warning}<div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-primary" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Review &amp; Action</a></div>',
                        })

                    if manager.email:
                        mail_vals_list.append({
                            'subject': f'Action Required: Approve Voucher {rec.name}',
                            'email_from': '<noreply@researchayu.com>',
                            'email_to': manager.email,
                            'body_html': f"""<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;"><h2 style="color: #333;">Voucher Approval Required</h2><p style="color: #555; font-size: 16px;">Hello {escape(manager.name)},</p><p style="color: #555; font-size: 16px;">A new operational fund disbursement requires your immediate review based on rule: <strong>{escape(matched_rule.name)}</strong>.</p>{cross_cluster_warning}<table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;"><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Voucher:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(rec.name)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Clinic:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(active_clinic.name)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Category:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(rec.display_category)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Amount:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹{rec.amount}</td></tr></table><div style="text-align: center; margin-top: 30px;"><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold; display: inline-block;">Review &amp; Action Voucher</a></div></div>""",
                        })

                if task_vals_list:
                    self.env['project.task'].sudo().create(task_vals_list)
                if mail_vals_list:
                    self.env['mail.mail'].sudo().create(mail_vals_list)

    def _cleanup_todo_tasks(self, task_name_prefix):
        if 'project.task' in self.env:
            for rec in self:
                tasks = self.env['project.task'].sudo().search([('name', '=', f'{task_name_prefix} {rec.name}')])
                if tasks: tasks.write({'active': False})

    def action_approve(self):
        mail_vals_list = []
        for rec in self:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            if rec.amount > active_clinic.op_fund_balance: raise ValidationError(
                _("Insufficient funds in the clinic's operational fund! Available balance is ₹%s") % active_clinic.op_fund_balance)
            rec.state = 'approved'
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id, 'date': rec.date, 'transaction_type': 'debit', 'amount': rec.amount,
                'reference': f'Disbursement: {rec.name} - {rec.display_category}',
                'user_id': self.env.user.id
            })
            rec.activity_unlink(['mail.activity_data_todo'])
            self._cleanup_todo_tasks('Approve Voucher')
            active_clinic.sudo()._check_low_balance_alert()
            
            if rec.create_uid and rec.create_uid.email:
                mail_vals_list.append({
                    'subject': f'Approved: Voucher {rec.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': rec.create_uid.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #28a745;">Voucher Approved</h2><p>Hello,</p><p>Your voucher <strong>{escape(rec.name)}</strong> for ₹{rec.amount} has been approved.</p></div>""",
                    'state': 'outgoing',
                })
                
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    def action_mark_as_paid(self):
        mail_vals_list = []
        for rec in self:
            if not rec.payment_screenshot:
                raise ValidationError(
                    _("Auditing Restriction: You must attach an image or PDF copy of the finalized bank transaction rollout receipt to mark this voucher as Paid."))
            rec.state = 'paid'
            
            if rec.create_uid and rec.create_uid.email:
                mail_vals_list.append({
                    'subject': f'Paid: Voucher {rec.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': rec.create_uid.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #17a2b8;">Voucher Paid</h2><p>Hello,</p><p>Your voucher <strong>{escape(rec.name)}</strong> has been finalized and marked as paid.</p></div>""",
                    'state': 'outgoing',
                })
                
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    def action_unlock_for_correction(self):
        self.ensure_one()
        raise ValidationError(_("Auditing Restriction: Vouchers cannot be unlocked for correction once they have been approved or paid."))

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
            if rec.state != 'draft': raise ValidationError(_("Only draft vouchers can be deleted."))
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
            if rec.state in ('approved', 'paid'):
                raise ValidationError(
                    _("Auditing Restriction: Vouchers cannot be reset to draft once they have been approved or paid."))
            elif rec.state == 'rejected':
                # 🚨 ARCHITECTURE BUG FIXED: Do not copy binary data directly as it orphans/duplicates attachments
                # Instead, re-link the existing ir.attachment record to the new field
                att = self.env['ir.attachment'].sudo().search([
                    ('res_model', '=', self._name),
                    ('res_id', '=', rec.id),
                    ('res_field', '=', 'signed_voucher_file')
                ], limit=1)
                
                if att:
                    att.sudo().write({'res_field': 'old_signed_voucher_file'})
                
                # Invalidate cache to ensure ORM reads the updated attachment link
                rec.invalidate_recordset(['signed_voucher_file', 'old_signed_voucher_file'])
                
                rec.old_signed_voucher_filename = rec.signed_voucher_filename
                rec.signed_voucher_file = False
                rec.signed_voucher_filename = False
            rec.state = 'draft'

    def action_request_refund(self):
        for rec in self:
            if rec.state not in ('approved', 'paid'): raise ValidationError(
                _("Only authorized or paid vouchers can be submitted for a refund."))
            rec.state = 'refund_requested'
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            if active_clinic.op_fund_manager_ids:
                base_url = self.get_base_url()
                deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
                deadline = fields.Date.context_today(self) + timedelta(days=1)
                for manager in active_clinic.op_fund_manager_ids:
                    rec.activity_schedule('mail.activity_data_todo', user_id=manager.id,
                                          summary='Review Refund Request',
                                          note=f'A refund has been requested for Voucher {rec.name}.')
                    if 'project.task' in self.env:
                        self.env['project.task'].sudo().create({
                            'name': f'Review Refund {rec.name}', 'user_ids': [(4, manager.id)],
                            'date_deadline': deadline, 'is_voucher_task': True,
                            'description': f'<p>A refund request for Voucher <strong>{rec.name}</strong> (₹{rec.amount}) requires your review.</p><br/><div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-warning" style="background-color: #f0ad4e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; margin-top: 10px;">Click Here to Action Refund</a></div>',
                        })

    def action_approve_refund(self):
        mail_vals_list = []
        for rec in self:
            if rec.state != 'refund_requested': raise ValidationError(_("Refund must be requested first."))
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id
            self.env['operational.fund.audit'].sudo().create({
                'clinic_id': active_clinic.id, 'date': fields.Date.context_today(self), 'transaction_type': 'credit',
                'amount': rec.amount,
                'reference': f'Refund: Fully Reclaimed Voucher {rec.name}', 'user_id': self.env.user.id
            })
            rec.state = 'refunded'
            rec.activity_unlink(['mail.activity_data_todo'])
            self._cleanup_todo_tasks('Review Refund')
            active_clinic.sudo()._check_low_balance_alert()
            
            if rec.create_uid and rec.create_uid.email:
                mail_vals_list.append({
                    'subject': f'Refund Approved: Voucher {rec.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': rec.create_uid.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #28a745;">Refund Approved</h2><p>Hello,</p><p>The refund for voucher <strong>{escape(rec.name)}</strong> has been approved.</p></div>""",
                    'state': 'outgoing',
                })
                
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    def action_cancel_refund(self):
        mail_vals_list = []
        for rec in self:
            rec.state = 'approved'
            rec.activity_unlink(['mail.activity_data_todo'])
            self._cleanup_todo_tasks('Review Refund')
            
            if rec.create_uid and rec.create_uid.email:
                mail_vals_list.append({
                    'subject': f'Refund Denied: Voucher {rec.name}',
                    'email_from': '<noreply@researchayu.com>',
                    'email_to': rec.create_uid.email,
                    'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #d9534f;">Refund Denied</h2><p>Hello,</p><p>The refund request for voucher <strong>{escape(rec.name)}</strong> was denied.</p></div>""",
                    'state': 'outgoing',
                })
                
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

    def action_sync_pending_alerts(self):
        legacy_escalated_cats = ['therapist_incentive', 'therapist_overtime', 'home_visit_travel',
                                 'fixed_therapist_travel', 'floater_travel', 'clinic_to_clinic']
        
        waiting_recs = self.filtered(lambda r: r.state == 'waiting')
        if not waiting_recs:
            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Sync Complete'), 'message': _('No pending vouchers found to sync.'), 'sticky': False, 'type': 'info'}}

        base_url = self.get_base_url()
        deadline = fields.Date.context_today(self) + timedelta(days=1)
        
        # 1. Bulk Lookup for existing tasks to prevent N+1 Queries
        existing_task_map = {}
        if 'project.task' in self.env:
            task_names = [f'Approve Voucher {rec.name}' for rec in waiting_recs]
            existing_tasks = self.env['project.task'].sudo().search([
                ('name', 'in', task_names),
                ('active', '=', True)
            ])
            for t in existing_tasks:
                existing_task_map.setdefault(t.name, []).extend(t.user_ids.ids)

        task_vals_list = []
        mail_vals_list = []

        for rec in waiting_recs:
            active_clinic = rec.clinic_id.master_fund_id or rec.clinic_id

            if rec.expense_category:
                is_escalated = rec.expense_category in ['incentive', 'overtime', 'travel']
            else:
                is_escalated = rec.category in legacy_escalated_cats

            target_managers = active_clinic.op_fund_ho_manager_ids if is_escalated else active_clinic.op_fund_manager_ids

            if target_managers:
                deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
                task_summary = '🏢 Head Office: Review Voucher (Synced)' if is_escalated else 'Review Urgent Voucher (Synced)'
                cross_cluster_warning = f'<p style="color: #d9534f; font-weight: bold;">⚠️ Cross-Cluster Alert: Patient is registered at {rec.home_visit_patient_clinic}.</p>' if rec.is_cross_cluster_visit else ''
                task_name = f'Approve Voucher {rec.name}'

                for manager in target_managers:
                    # O(1) Memory check instead of O(N) database queries
                    if manager.id in existing_task_map.get(task_name, []):
                        continue

                    # Fallback Schedule Activity
                    rec.activity_schedule('mail.activity_data_todo', user_id=manager.id, summary=task_summary,
                                          note=f'Voucher {rec.name} requires your approval. <a href="{deep_link}">Click here to view</a>')
                    
                    if 'project.task' in self.env:
                        task_vals_list.append({
                            'name': task_name, 'user_ids': [(4, manager.id)],
                            'date_deadline': deadline, 'is_voucher_task': True,
                            'description': f'<p>Voucher <strong>{rec.name}</strong> for ₹{rec.amount} has been submitted by {escape(active_clinic.name)}.</p>{cross_cluster_warning}<div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-primary" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; margin-top: 10px;">Click Here to Review &amp; Action</a></div>',
                        })
                    
                    if manager.email:
                        mail_vals_list.append({
                            'subject': f'Action Required: Approve Voucher {rec.name} (Synced)',
                            'email_from': '<noreply@researchayu.com>',
                            'email_to': manager.email,
                            'body_html': f"""<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;"><h2 style="color: #333;">Voucher Approval Required</h2><p style="color: #555; font-size: 16px;">Hello {escape(manager.name)},</p><p style="color: #555; font-size: 16px;">This is a synced notification for a pending operational fund disbursement.</p>{cross_cluster_warning}<table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;"><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Voucher:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(rec.name)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Clinic:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(active_clinic.name)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Amount:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹{rec.amount}</td></tr></table><div style="text-align: center; margin-top: 30px;"><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold; display: inline-block;">Review &amp; Action Voucher</a></div></div>""",
                            'state': 'outgoing',
                        })

        # 2. Bulk Create Records
        if task_vals_list:
            self.env['project.task'].sudo().create(task_vals_list)
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list)

        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Sync Complete'),
                                                                                       'message': _(
                                                                                           'Approval tasks and emails have been retroactively dispatched.'),
                                                                                       'sticky': False,
                                                                                       'type': 'success'}}

    def action_backup_to_s3(self):
        for rec in self:
            pdf_name = f"Voucher_{rec.name.replace('/', '_')}.pdf"
            attachment = self.env['ir.attachment'].search([
                ('res_model', '=', 'operational.fund.disbursement'),
                ('res_id', '=', rec.id),
                ('name', '=', pdf_name)
            ], limit=1)

            if not attachment and rec.state in ['approved', 'paid', 'refunded', 'refund_requested']:
                try:
                    report = self.env['ir.actions.report']._get_report_from_name(
                        'operational_fund.report_voucher_template')
                    pdf_content, _ = report.sudo()._render_qweb_pdf(rec.id)
                    self.env['ir.attachment'].sudo().create({
                        'name': pdf_name, 'type': 'binary', 'raw': pdf_content,
                        'res_model': 'operational.fund.disbursement', 'res_id': rec.id, 'mimetype': 'application/pdf'
                    })
                except Exception as e:
                    _logger.error(f"Failed to generate backup PDF for {rec.name}: {str(e)}")

            local_attachments = self.env['ir.attachment'].search([
                ('res_model', '=', 'operational.fund.disbursement'),
                ('res_id', '=', rec.id),
                ('is_s3_stored', '=', False),
                ('type', '=', 'binary')
            ])
            if local_attachments: local_attachments._force_s3_upload()

        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Backup Complete'),
                                                                                       'message': _(
                                                                                           'Missing PDFs were generated and files safely mirrored to S3.'),
                                                                                       'sticky': False,
                                                                                       'type': 'success'}}

    def action_bulk_download_assets(self):
        if not self:
            return False

        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)

        csv_writer.writerow([
            'Voucher Number', 'Date', 'Clinic Branch', 'Amount', 'Status',
            'S3 Receipt URL', 'S3 Voucher URL', 'S3 Payment URL'
        ])

        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as temp_zip:
            with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for rec in self:
                    clean_code = rec.name.replace('/', '_')
                    clinic_name = rec.clinic_id.name if rec.clinic_id else 'Unknown Branch'

                    # Security: Sanitize against CSV Formula Injection
                    safe_name = f"'{rec.name}" if rec.name and str(rec.name).startswith(('=', '+', '-', '@')) else rec.name
                    safe_clinic_name = f"'{clinic_name}" if clinic_name and str(clinic_name).startswith(('=', '+', '-', '@')) else clinic_name

                    csv_writer.writerow([
                        safe_name, str(rec.date or ''), safe_clinic_name, rec.amount,
                        rec.state or 'draft', rec.s3_receipt_url or 'N/A',
                        rec.s3_voucher_url or 'N/A', rec.s3_payment_url or 'N/A'
                    ])

                    def write_document_or_placeholder(field_name, filename_suffix, s3_url, default_ext='pdf'):
                        filename = f"{clean_code}_{filename_suffix}.{default_ext}"

                        att = self.env['ir.attachment'].sudo().search([
                            ('res_model', '=', 'operational.fund.disbursement'),
                            ('res_id', '=', rec.id),
                            ('res_field', '=', field_name)
                        ], limit=1)

                        if att:
                            # 🚨 ARCHITECTURE UPGRADE: If stored in S3, DO NOT fetch synchronously.
                            # The user gets the short-lived presigned URL in the CSV instead.
                            if att.is_s3_stored:
                                msg = f"This file is safely stored in AWS S3.\nTo prevent server timeouts during bulk exports, the physical file was not embedded in this ZIP.\nPlease use the secure download link provided in the audit_manifest.csv to view it.\n\nLink: {s3_url or 'N/A'}"
                                zip_file.writestr(f"{clean_code}_{filename_suffix}_S3_LINK.txt", msg.encode('utf-8'))
                                return
                            
                            # Standard Local DB File Handling
                            if att.raw or att.datas:
                                data = att.raw or base64.b64decode(att.datas)
                                if att.name and '.' in att.name:
                                    filename = f"{clean_code}_{filename_suffix}.{att.name.split('.')[-1]}"
                                
                                if isinstance(data, str):
                                    try:
                                        data = base64.b64decode(data)
                                    except Exception:
                                        data = data.encode('utf-8')
                                zip_file.writestr(filename, data)
                                return

                        # No file attached at all
                        missing_filename = f"{clean_code}_{filename_suffix}_MISSING.txt"
                        msg = f"Auditing Notice: No physical document or file payload was uploaded for {filename_suffix} under voucher reference {rec.name} at the time of export.\n"
                        zip_file.writestr(missing_filename, msg.encode('utf-8'))

                    write_document_or_placeholder('receipt_file', 'receipt', rec.s3_receipt_url, 'jpg')
                    write_document_or_placeholder('signed_voucher_file', 'voucher', rec.s3_voucher_url, 'pdf')
                    write_document_or_placeholder('payment_screenshot', 'payment_proof', rec.s3_payment_url, 'jpg')

                csv_buffer.seek(0)
                zip_file.writestr('audit_manifest.csv', csv_buffer.getvalue().encode('utf-8'))

            temp_zip.flush()
            
        with open(temp_zip.name, 'rb') as f:
            archive_attachment = self.env['ir.attachment'].sudo().create({
                'name': 'OFD_Bulk_Financial_Export.zip',
                'type': 'binary',
                'raw': f.read(),
                'mimetype': 'application/zip',
                'public': False
            })

        try:
            os.unlink(temp_zip.name)
        except Exception as e:
            _logger.warning(f"Failed to delete temp zip file {temp_zip.name}: {e}")

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{archive_attachment.id}?download=true',
            'target': 'self'
        }

    @api.constrains('date')
    def _check_voucher_date_is_today(self):
        for rec in self:
            if rec.date and rec.date != fields.Date.context_today(self):
                raise  ValidationError(_("Auditing Restriction: Vouchers can only be created for today's date."))

    def unlink(self):
        for rec in self:
            if rec.state != 'draft':
                raise ValidationError(
                    _("Auditing Security: You can only delete disbursement vouchers that are still in the 'Draft' state."))
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
                        _("Auditing Security: You cannot alter automated financial approval tasks."))
        return super().write(vals)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    op_fund_s3_bucket = fields.Char(string="S3 Bucket Name", config_parameter='operational_fund.s3_bucket')
    op_fund_s3_access_key = fields.Char(string="AWS Access Key", config_parameter='operational_fund.s3_access_key')
    op_fund_s3_secret_key = fields.Char(string="AWS Secret Key", config_parameter='operational_fund.s3_secret_key')
    op_fund_s3_region = fields.Char(string="AWS Region", default='ap-south-1',
                                    config_parameter='operational_fund.s3_region')


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    is_s3_stored = fields.Boolean(string="Stored in AWS S3", default=False, index=True)
    s3_object_key = fields.Char(string="AWS S3 Object Key")

    @api.model
    def _get_s3_credentials(self):
        if boto3 is None:
            raise ValidationError(_("System Architecture Error: The Python 'boto3' library is missing. S3 operations cannot proceed. Please install boto3."))

        # SECURITY FIX: Prioritize secure odoo.conf or OS environment variables for sensitive keys
        bucket = config.get('op_fund_s3_bucket') or os.environ.get('AWS_S3_BUCKET') or self.env['ir.config_parameter'].sudo().get_param('operational_fund.s3_bucket')
        
        # Never store or fetch AWS Secret keys from plaintext ir.config_parameter database table
        access_key = config.get('op_fund_s3_access_key') or os.environ.get('AWS_ACCESS_KEY_ID')
        secret_key = config.get('op_fund_s3_secret_key') or os.environ.get('AWS_SECRET_ACCESS_KEY')
        
        region = config.get('op_fund_s3_region') or os.environ.get('AWS_DEFAULT_REGION') or self.env['ir.config_parameter'].sudo().get_param('operational_fund.s3_region', 'ap-south-1')
        custom_endpoint = config.get('op_fund_s3_endpoint_url') or os.environ.get('AWS_S3_ENDPOINT_URL')

        if not bucket: return None, None
        try:
            client_kwargs = {'region_name': region}
            if access_key and secret_key:
                client_kwargs['aws_access_key_id'] = access_key
                client_kwargs['aws_secret_access_key'] = secret_key
            if custom_endpoint:
                client_kwargs['endpoint_url'] = custom_endpoint
                
            s3_client = boto3.client('s3', **client_kwargs)
            return s3_client, bucket
        except Exception as e:
            _logger.error(f"AWS S3 Client Initialization Failed: {str(e)}")
            raise ValidationError(_(f"AWS S3 Client Initialization Failed: {str(e)}"))

    def unlink(self):
        for attachment in self:
            if attachment.res_model == 'operational.fund.disbursement' and attachment.res_id:
                disb = self.env['operational.fund.disbursement'].browse(attachment.res_id)
                if disb.exists() and disb.state in ('approved', 'paid', 'refund_requested', 'refunded'):
                    if not self.env.su:
                        raise ValidationError(
                            _("Auditing Security: You cannot delete attachments from a finalized operational disbursement."))

        if boto3:
            try:
                s3_client, bucket = self._get_s3_credentials()
                if s3_client and bucket:
                    for attachment in self:
                        if attachment.is_s3_stored and attachment.s3_object_key:
                            try:
                                s3_client.delete_object(Bucket=bucket, Key=attachment.s3_object_key)
                            except Exception as e:
                                _logger.error(f"Failed to delete orphaned S3 object {attachment.s3_object_key}: {e}")
                                raise ValidationError(_("Cloud Architecture Error: Failed to delete the asset from AWS S3. Aborting local deletion to prevent data orphaning."))
            except Exception as outer_e:
                _logger.error(f"Could not connect to S3 to delete orphaned objects: {outer_e}")
                raise ValidationError(_("Cloud Architecture Error: Could not connect to AWS S3. Aborting deletion to prevent orphaned assets."))

        return super().unlink()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not boto3: return records

        s3_client, bucket = self._get_s3_credentials()
        if not s3_client or not bucket: return records

        protected_models = ['operational.fund.disbursement', 'operational.fund.allocation']
        for rec in records:
            if rec.res_model in protected_models and rec.type == 'binary' and rec.raw:
                try:
                    file_extension = mimetypes.guess_extension(rec.mimetype) or '.bin'
                    object_key = f"operational_funds/{rec.res_model}/{rec.res_id}_{rec.id}{file_extension}"
                    s3_client.put_object(Bucket=bucket, Key=object_key, Body=rec.raw, ContentType=rec.mimetype)
                    rec.sudo().write({'is_s3_stored': True, 's3_object_key': object_key})
                except Exception as e:
                    _logger.error(f"AWS S3 Cloud Upload Failure for asset {rec.id}: {str(e)}")
                    raise ValidationError(_("Cloud Architecture Error: Failed to upload the asset to AWS S3. Transaction aborted to maintain cloud sync integrity."))
        return records

    @api.depends('store_fname', 'db_datas', 'file_size')
    def _compute_raw(self):
        super()._compute_raw()
        # OPTIMIZED: Respect Odoo's bin_size context. If the frontend only wants the size
        # (like in list/kanban views), do NOT trigger an expensive synchronous AWS download.
        if self.env.context.get('bin_size'):
            return
        if boto3:
            s3_client, bucket = self._get_s3_credentials()
            if s3_client and bucket:
                for attach in self:
                    if attach.is_s3_stored and attach.s3_object_key:
                        try:
                            s3_object = s3_client.get_object(Bucket=bucket, Key=attach.s3_object_key)
                            attach.raw = s3_object['Body'].read()
                        except Exception as e:
                            _logger.error(
                                f"Failed to stream down asset from S3 bucket via key {attach.s3_object_key}: {str(e)}")

    def _force_s3_upload(self):
        if not boto3: return
        s3_client, bucket = self._get_s3_credentials()
        if not s3_client or not bucket: return

        for rec in self:
            if not rec.is_s3_stored and rec.raw:
                try:
                    file_extension = mimetypes.guess_extension(rec.mimetype) or '.bin'
                    object_key = f"operational_funds/{rec.res_model}/{rec.res_id}_{rec.id}{file_extension}"
                    s3_client.put_object(Bucket=bucket, Key=object_key, Body=rec.raw, ContentType=rec.mimetype)
                    rec.sudo().write({'is_s3_stored': True, 's3_object_key': object_key})
                except Exception as e:
                    _logger.error(f"Force Migration Failure for asset {rec.id}: {str(e)}")

    @api.model
    def action_migrate_local_attachments_to_s3(self):
        local_attachments = self.search(
            [('res_model', 'in', ['operational.fund.disbursement', 'operational.fund.allocation']),
             ('is_s3_stored', '=', False), ('type', '=', 'binary')])
        if local_attachments:
            local_attachments._force_s3_upload()
            return {'type': 'ir.actions.client', 'tag': 'display_notification',
                    'params': {'title': _('Migration Successful'),
                               'message': _('%s attachments safely synced to S3 bucket.') % len(local_attachments),
                               'sticky': False, 'type': 'success'}}
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('System Synced'), 'message': _('No outstanding unmigrated files were found.'),
                           'sticky': False, 'type': 'warning'}}


class OperationalFundApprovalRule(models.Model):
    _name = 'operational.fund.approval.rule'
    _description = 'Disbursement Approval Rule Engine'
    _order = 'sequence, id'

    name = fields.Char(string='Rule Name', required=True, help="e.g., 'Auto-Approve Office Expenses < 500'")
    sequence = fields.Integer(string='Priority Sequence', default=10, help="Lower numbers are evaluated first.")
    active = fields.Boolean(default=True)

    # The Ultimate Customization Trigger: Odoo's Native Domain Builder
    model_id = fields.Many2one('ir.model', string='Model', default=lambda self: self.env.ref(
        'operational_fund.model_operational_fund_disbursement').id, readonly=True)
    model_name = fields.Char(related='model_id.model', string='Model Name', readonly=True)
    domain = fields.Char(string='Conditions (IF)', default='[]', required=True,
                         help="Define the exact conditions for this rule to trigger.")

    # The Outcomes (THEN)
    action_type = fields.Selection([
        ('auto_approve', 'Auto-Approve (Bypass Review)'),
        ('require_approval', 'Require Human Approval'),
        ('block', 'Block & Reject Submission')
    ], string='Action Outcome', required=True, default='require_approval')

    approver_ids = fields.Many2many('res.users', 'op_fund_rule_approver_rel', string='Assigned Approvers',
                                    help="Users who must approve this voucher.")
    cc_user_ids = fields.Many2many('res.users', 'op_fund_rule_cc_rel', string='CC / Notify Users',
                                   help="Users who will be silently added as followers for auditing.")

    block_message = fields.Text(string='Rejection Message',
                                help="The error message shown to the user if this rule blocks their submission.")