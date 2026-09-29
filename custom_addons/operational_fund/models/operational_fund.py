import io
import logging
import mimetypes
import zipfile
import base64
import xlsxwriter
import csv
import os
import tempfile
from markupsafe import escape
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import timedelta
from odoo.tools.safe_eval import safe_eval
from odoo.tools import config
from markupsafe import escape, Markup

try:
    import boto3
except ImportError:
    boto3 = None

_logger = logging.getLogger(__name__)


class Clinic(models.Model):
    _inherit = 'clinic.clinic'

    op_fund_manager_ids = fields.Many2many(
        comodel_name='res.users',
        relation='clinic_op_fund_manager_rel',
        column1='clinic_id',
        column2='user_id',
        string='Standard Fund Managers',
        help="Managers designated to approve vouchers for this specific clinic."
    )

    # def _check_low_balance_alert(self):
    #     for clinic in self:
    #         if clinic.op_fund_alert_threshold > 0:
    #             if clinic.op_fund_balance <= clinic.op_fund_alert_threshold and not clinic.is_low_balance_alert_sent:
    #                 clinic._send_low_balance_notification()
    #                 clinic.is_low_balance_alert_sent = True
    #             elif clinic.op_fund_balance > clinic.op_fund_alert_threshold and clinic.is_low_balance_alert_sent:
    #                 clinic.is_low_balance_alert_sent = False

    # def _send_low_balance_notification(self):
    #     mail_vals_list = []
    #     for clinic in self:
    #         # Refined Audit Scope: Only alert standard managers and finance teams directly related to this clinic
    #         target_users = self.env.ref('operational_fund.group_op_fund_manager').users | self.env.ref('operational_fund.group_op_fund_controller').users
    #         if not target_users:
    #             continue
    #
    #         subject = f"⚠️ URGENT: Low Balance Alert for {clinic.name}"
    #         body = f"""
    #             <div style="font-family: Arial, sans-serif; max-width: 600px; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
    #                 <h2 style="color: #d9534f;">Operational Fund Low Balance Warning</h2>
    #                 <p style="color: #555; font-size: 16px;">The operational fund balance for <strong>{escape(clinic.name)}</strong> has dropped below the minimum safety threshold.</p>
    #                 <table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;">
    #                     <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Current Balance:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹{clinic.op_fund_balance}</td></tr>
    #                     <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Alert Threshold:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; font-weight: bold;">₹{clinic.op_fund_alert_threshold}</td></tr>
    #                 </table>
    #                 <div style="background-color: #fcf8e3; color: #8a6d3b; padding: 15px; border-radius: 4px; border: 1px solid #faebcc;">
    #                     <strong>Action Required:</strong> Please arrange for a wallet top-up as soon as possible to avoid disruption of clinic operations.
    #                 </div>
    #             </div>
    #         """
    #
    #         emails = [u.email for u in target_users if u.email]
    #         if emails:
    #             mail_vals_list.append({
    #                 'subject': subject,
    #                 'email_from': '<noreply@researchayu.com>',
    #                 'email_to': ','.join(emails),
    #                 'body_html': body,
    #                 'state': 'outgoing',
    #             })
    #
    #     if mail_vals_list:
    #         self.env['mail.mail'].sudo().create(mail_vals_list).send()


class OperationalFundDisbursement(models.Model):
    _name = 'operational.fund.disbursement'
    _description = 'Operational Fund Disbursement'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Voucher Number', default='New', readonly=True)
    clinic_id = fields.Many2one('clinic.clinic', string='Clinic', required=True, tracking=True, index=True,
                                default=lambda self: self.env.user.clinic_id.id if hasattr(self.env.user,
                                                                                           'clinic_id') else False)
    is_today = fields.Boolean(string="Is Today's Voucher", compute='_compute_is_today', search='_search_is_today')

    expense_category = fields.Selection(
        [('incentive', 'Therapist Incentive'), ('overtime', 'Therapist Overtime'), ('travel', 'Travel & Commute'),
         ('office', 'Office & Clinic Expenses'), ('other', 'Other Expense')], string='Main Category', tracking=True,
        index=True)
    therapist_role = fields.Selection(
        [('home', 'Home Therapist'), ('fixed', 'Fixed Therapist'), ('floater', 'Floater Therapist')],
        string='Therapist Role', tracking=True)
    travel_type = fields.Selection(
        [('home', 'Home Visit Travel'), ('fixed', 'Fixed Therapist Travel'), ('floater', 'Floater Travel'),
         ('c2c', 'Clinic to Clinic Travel')], string='Travel Route', tracking=True)
    office_expense_type = fields.Selection(
        [('electricity', 'Electricity Bill'), ('water', 'Water Supply'), ('internet', 'Internet / Phone'),
         ('rent', 'Rent'), ('electrician', 'Electrician Charges'), ('plumber', 'Plumber Charges'),
         ('carpenter', 'Carpenter Charges'), ('stationary', 'Stationary'), ('printer_ink', 'Printer Ink'),
         ('cleaning_materials', 'Cleaning Materials'), ('biowaste_bags', 'Biowaste Bags')], string='Expense Type',
        tracking=True)
    display_category = fields.Char(string='Category', compute='_compute_display_category', store=True)

    # Legacy Fields (Kept for historical view)
    category = fields.Selection(
        [('therapist_incentive', 'Therapist Incentive'), ('therapist_overtime', 'Therapist Overtime'),
         ('home_visit_travel', 'Home Visit Travelling'), ('fixed_therapist_travel', 'Fixed Therapist Travelling'),
         ('floater_travel', 'Floater Travelling'), ('clinic_to_clinic', 'Clinic to Clinic Travelling'),
         ('electricity', 'Electricity Bill'), ('water', 'Water Supply'), ('internet', 'Internet / Phone'),
         ('rent', 'Rent'), ('electrician', 'Electrician Charges'), ('plumber', 'Plumber Charges'),
         ('carpenter', 'Carpenter Charges'), ('stationary', 'Stationary'), ('printer_ink', 'Printer Ink'),
         ('cleaning_materials', 'Cleaning Materials'), ('biowaste_bags', 'Biowaste Bags'), ('cake', 'Cake (Legacy)'),
         ('decorations', 'Decorations (Legacy)'), ('other', 'Other Expense')], string='Legacy Category', tracking=True)
    payee_type = fields.Selection([('internal', 'Internal Employee'), ('external', 'External Vendor')],
                                  string='Legacy Payee Type', tracking=True)

    therapist_name = fields.Char(string='Therapist Name', tracking=True)
    vendor_name = fields.Char(string='Vendor / Payee Name', tracking=True)

    date = fields.Date(
        string='Date',
        default=fields.Date.context_today,
        required=True,
        readonly=True,  # Added this to lock the field
        tracking=True,
        index=True
    )

    approval_date = fields.Date(
        string='Approval Date',
        readonly=True,
        tracking=True,
        index=True,
        copy=False
    )

    # New Strict Relational Fields
    therapist_ref_id = fields.Many2one('clinic.therapist', string='Therapist (Linked)', tracking=True)
    vendor_ref_id = fields.Many2one('operational.fund.vendor', string='Vendor (Linked)', tracking=True)
    utr_reference = fields.Char(string='Bank UTR Reference', tracking=True, readonly=True)
    debit_narr = fields.Char(string='Debit Narration', tracking=True, readonly=True)

    payee_display = fields.Char(string='Payee', compute='_compute_payee_display', store=True)
    amount = fields.Float(string='Amount', required=True, tracking=True)

    home_visit_mrn_search = fields.Char(string='Patient MRN Search', tracking=True)
    home_visit_patient_name = fields.Char(string='Patient Name', readonly=True)
    home_visit_patient_phone = fields.Char(string='Patient Phone', readonly=True)
    home_visit_patient_clinic = fields.Char(string='Registered Clinic', readonly=True)
    is_cross_cluster_visit = fields.Boolean(string='Is Cross-Cluster Visit', readonly=True, store=True)

    from_clinic_id = fields.Many2one('clinic.clinic', string='From Clinic', tracking=True)
    to_clinic_id = fields.Many2one('clinic.clinic', string='To Clinic', tracking=True)
    therapist_type = fields.Selection([('fixed', 'Fixed Therapist'), ('floater', 'Floater')],
                                      string='Therapist Type',  # Changed from 'Therapist Role'
                                      tracking=True)
    other_expense_details = fields.Char(string='Specify Other Expense', tracking=True)
    description = fields.Text(string='Business Purpose')

    receipt_file = fields.Binary(string='Receipt Attachment', attachment=True)
    receipt_filename = fields.Char(string='Receipt Filename', )
    is_receipt_mandatory = fields.Boolean(compute='_compute_is_receipt_mandatory')
    is_receipt_image = fields.Boolean(compute='_compute_is_receipt_image', store=True)

    signed_voucher_file = fields.Binary(string='Signed Voucher (Upload)', attachment=True)
    signed_voucher_filename = fields.Char(string='Signed Voucher Filename')
    is_signed_voucher_image = fields.Boolean(compute='_compute_is_signed_voucher_image', store=True)

    old_signed_voucher_file = fields.Binary(string='Original Signed Voucher (Archived)', readonly=True, attachment=True)
    old_signed_voucher_filename = fields.Char(string='Original Signed Voucher Filename')

    # DRAFT STATE REMOVED. Defaults to waiting.
    state = fields.Selection([
        ('draft', 'Draft'),
        ('waiting', 'Waiting Approval'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
        ('rejected', 'Rejected'),
        ('refund_requested', 'Refund Requested'),
        ('refunded', 'Refunded'),
    ], string='Status', default='draft', tracking=True, index=True)

    payment_screenshot = fields.Binary(string='Transaction Proof Screenshot', attachment=True)
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

    allowed_therapist_ids = fields.Many2many(
        'clinic.therapist',
        compute='_compute_allowed_therapists',
        string='Allowed Therapists'
    )
    is_system_generated = fields.Boolean(string="System Generated", default=False, readonly=True, copy=False)

    is_receipt_pdf = fields.Boolean(compute='_compute_document_file_types')
    is_signed_voucher_pdf = fields.Boolean(compute='_compute_document_file_types')

    # --- NEW DATA & ACCOUNTABILITY FIELDS ---
    created_by_name = fields.Char(string='Generated By', default=lambda self: self.env.user.name, readonly=True)
    therapist_ved_number = fields.Char(string='VED Number', tracking=True)
    therapist_phone = fields.Char(string='Phone Number', tracking=True)
    bank_name = fields.Char(string='Bank Name', tracking=True)
    bank_account_name = fields.Char(string='Bank Account Name', tracking=True)
    bank_account_number = fields.Char(string='Account Number', tracking=True)
    bank_ifsc_code = fields.Char(string='IFSC Code', tracking=True)


    who_paid = fields.Char(string='Paid By (Name)', tracking=True)
    proof_attachment_ids = fields.Many2many('ir.attachment', string='Additional Proofs')

    # --- HIGHLY CUSTOMIZABLE FREEZE CONTROLLER ---
    is_frozen = fields.Boolean(string="Is Frozen", compute='_compute_is_frozen', store=True)

    @api.model
    def _get_default_clinics(self):
        """Fetches the authorized clinic IDs instantly for the dropdown default."""
        if self.env.user.has_group('operational_fund.group_op_fund_controller') or self.env.user.has_group(
                'base.group_system'):
            return self.env['clinic.clinic'].search([]).ids
        return self._get_user_clinic_ids(self.env.user)

    # Adding the default parameter solves the "No records" bug on New forms
    allowed_clinic_ids = fields.Many2many(
        'clinic.clinic',
        compute='_compute_allowed_clinics',
        default=lambda self: self._get_default_clinics()
    )

    @api.depends_context('uid')
    def _compute_allowed_clinics(self):
        """Calculates which clinics the current user is allowed to see in the dropdown."""
        allowed_clinics = self.env['clinic.clinic'].browse(self._get_default_clinics())
        for rec in self:
            rec.allowed_clinic_ids = allowed_clinics

    @api.depends('state')
    def _compute_is_frozen(self):
        """Centralized control for freezing the voucher.
        If you ever need to change when a voucher locks, you only change it here."""
        for rec in self:
            rec.is_frozen = rec.state != 'draft'

    # --- AUTO-POPULATION LOGIC ---
    @api.onchange('therapist_ref_id')
    def _onchange_therapist_data(self):
        """Strictly extracts from the Clinic Schedule Therapist Directory."""
        if self.therapist_ref_id:
            self.therapist_name = self.therapist_ref_id.name
            self.therapist_ved_number = getattr(self.therapist_ref_id, 'vendor_id', '')
            self.therapist_phone = getattr(self.therapist_ref_id, 'contact_number', '')
            self.bank_name = getattr(self.therapist_ref_id, 'bank_name', '')
            self.bank_account_name = getattr(self.therapist_ref_id, 'bank_account_name', '')
            self.bank_account_number = getattr(self.therapist_ref_id, 'bank_account_number', '')
            self.bank_ifsc_code = getattr(self.therapist_ref_id, 'bank_ifsc_code', '')

    @api.onchange('vendor_ref_id')
    def _onchange_vendor_data(self):
        """Strictly extracts from the Operational Funds Vendor Directory."""
        if self.vendor_ref_id:
            self.vendor_name = self.vendor_ref_id.name
            self.bank_name = getattr(self.vendor_ref_id, 'bank_name', '')
            self.bank_account_name = getattr(self.vendor_ref_id, 'bank_account_name', '')
            self.bank_account_number = getattr(self.vendor_ref_id, 'bank_account_number', '')
            self.bank_ifsc_code = getattr(self.vendor_ref_id, 'bank_ifsc_code', '')

    @api.model_create_multi
    def create(self, vals_list):
        # 1. Pre-fetch clinic records in bulk to prevent N+1 queries during naming
        clinic_ids = list({v.get('clinic_id') for v in vals_list if v.get('clinic_id')})
        clinics = self.env['clinic.clinic'].browse(clinic_ids).exists()
        clinic_map = {c.id: c.name for c in clinics}

        for vals in vals_list:
            # A. Directory auto-population (Therapist)
            if vals.get('therapist_ref_id'):
                therapist = self.env['clinic.therapist'].browse(vals['therapist_ref_id'])
                if therapist.exists():
                    vals.setdefault('therapist_name', therapist.name or '')
                    vals.setdefault('therapist_ved_number', getattr(therapist, 'vendor_id', '') or '')
                    vals.setdefault('therapist_phone', getattr(therapist, 'contact_number', '') or '')
                    vals.setdefault('bank_name', getattr(therapist, 'bank_name', '') or '')
                    vals.setdefault('bank_account_name', getattr(therapist, 'bank_account_name', '') or '')
                    vals.setdefault('bank_account_number', getattr(therapist, 'bank_account_number', '') or '')
                    vals.setdefault('bank_ifsc_code', getattr(therapist, 'bank_ifsc_code', '') or '')

            # B. Directory auto-population (Vendor)
            if vals.get('vendor_ref_id'):
                vendor = self.env['operational.fund.vendor'].browse(vals['vendor_ref_id'])
                if vendor.exists():
                    vals.setdefault('vendor_name', vendor.name or '')
                    vals.setdefault('bank_name', getattr(vendor, 'bank_name', '') or '')
                    vals.setdefault('bank_account_name', getattr(vendor, 'bank_account_name', '') or '')
                    vals.setdefault('bank_account_number', getattr(vendor, 'bank_account_number', '') or '')
                    vals.setdefault('bank_ifsc_code', getattr(vendor, 'bank_ifsc_code', '') or '')

            # C. Generate Structured Voucher Sequence Code
            if vals.get('name', 'New') == 'New':
                c_name = clinic_map.get(vals.get('clinic_id'), 'UNK')
                c_code = c_name.split(',')[-1].strip()[:3].upper() if ',' in c_name else c_name[:3].upper()
                date_val = vals.get('date') or fields.Date.context_today(self).strftime('%Y-%m-%d')
                try:
                    d_code = fields.Date.from_string(date_val).strftime('%d%m%y')
                except Exception:
                    d_code = '000000'

                main_cat = vals.get('expense_category') or vals.get('category') or 'OTH'
                main_code = 'OFF' if main_cat == 'office' else ('TRV' if main_cat == 'travel' else main_cat[:3].upper())
                sub_code = (vals.get('travel_type') or vals.get('office_expense_type') or
                            vals.get('therapist_role') or vals.get('therapist_type') or
                            vals.get('payee_type') or 'GEN')[:3].upper()

                seq = self.env['ir.sequence'].next_by_code('operational.fund.disbursement') or '0000'
                vals['name'] = f"{c_code}/{d_code}/{main_code}/{sub_code}/{seq}"

        records = super().create(vals_list)

        # D. Link proof attachments uploaded during record creation
        for record in records:
            if record.proof_attachment_ids:
                record.proof_attachment_ids.sudo().write({
                    'res_model': 'operational.fund.disbursement',
                    'res_id': record.id,
                })

        return records

    # --- BUTTON ACTION TO OPEN "PAID" WIZARD ---
    def action_mark_paid_wizard(self):
        self.ensure_one()
        return {
            'name': 'Mark Voucher as PAID',
            'type': 'ir.actions.act_window',
            'res_model': 'operational.fund.mark.paid.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_disbursement_id': self.id}
        }

    @api.depends('receipt_filename', 'signed_voucher_filename')
    def _compute_document_file_types(self):
        for rec in self:
            rec.is_receipt_pdf = bool(rec.receipt_filename and rec.receipt_filename.lower().endswith('.pdf'))
            rec.is_signed_voucher_pdf = bool(
                rec.signed_voucher_filename and rec.signed_voucher_filename.lower().endswith('.pdf'))


    # @api.constrains('expense_category', 'therapist_ref_id', 'date', 'is_system_generated')
    # def _prevent_duplicate_allowances(self):
    #     """Prevents Clinic Admins from manually creating duplicate OT/Incentives if they already exist."""
    #     for rec in self:
    #         if not rec.is_system_generated and rec.expense_category in ['incentive',
    #                                                                     'overtime'] and rec.therapist_ref_id:
    #             existing = self.search([
    #                 ('expense_category', '=', rec.expense_category),
    #                 ('therapist_ref_id', '=', rec.therapist_ref_id.id),
    #                 ('date', '=', rec.date),
    #                 ('id', '!=', rec.id),
    #                 ('state', '!=', 'rejected')
    #             ])
    #             if existing:
    #                 raise ValidationError(
    #                     _("Auditing Lock: An active %s voucher already exists for %s on this date. You cannot create a duplicate manual voucher.") % (
    #                         dict(self._fields['expense_category'].selection).get(rec.expense_category),
    #                         rec.therapist_ref_id.name
    #                     ))

    def action_submit_for_approval(self):
        # Optimization: Fetch rules once
        rules = self.env['operational.fund.approval.rule'].sudo().search([('active', '=', True)], order='sequence, id')
        for rec in self:
            if rec.amount <= 0: raise ValidationError(_("Disbursement amount must be strictly positive."))
            if rec.show_employee_payee and not rec.payee_id: raise ValidationError(
                _("Missing Parameter: Please select an Employee Profile."))
            if rec.show_therapist_name_input and not (rec.therapist_name or rec.therapist_ref_id):
                raise ValidationError(_("Missing Parameter: Please select or type the Therapist Name."))
            if rec.show_vendor_payee and not (rec.vendor_name or rec.vendor_ref_id):
                raise ValidationError(_("Missing Parameter: Please select or specify the Vendor Name."))
            if rec.show_home_visit and not rec.home_visit_mrn_search: raise ValidationError(
                _("Missing Compliance Parameter: You must enter the patient MRN code for home visits."))
            if not rec.signed_voucher_file: raise ValidationError(
                _("Hold on! You must download, sign, and upload the physical Disbursement Voucher before you can submit it."))
            if rec.is_receipt_mandatory and not rec.receipt_file: raise ValidationError(
                _("Strict Auditing Rule: You must upload the original vendor receipt/bill for this expense category before submitting!"))
            active_clinic = rec.clinic_id

            # Validate live balance BEFORE rules
            # if rec.amount > active_clinic.op_fund_balance:
            #     raise ValidationError(
            #         _("Insufficient funds in the clinic's operational fund! Available balance is ₹ %s") % active_clinic.op_fund_balance)

            #   THE RULES ENGINE EVALUATOR
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
                    body=Markup(
                        f"<strong>System Auto-Approved:</strong> Passed via automated rule <em>'{escape(matched_rule.name)}'</em>."),
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
                            'body_html': f"""<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;"><h2 style="color: #333;">Voucher Approval Required</h2><p style="color: #555; font-size: 16px;">Hello {escape(manager.name)},</p><p style="color: #555; font-size: 16px;">A new operational fund disbursement requires your immediate review based on rule: <strong>{escape(matched_rule.name)}</strong>.</p>{cross_cluster_warning}<table style="width: 100%; margin-top: 20px; margin-bottom: 20px; border-collapse: collapse;"><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Voucher:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(rec.name)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Clinic:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(active_clinic.name)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Category:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee;">{escape(rec.display_category)}</td></tr><tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><strong>Amount:</strong></td><td style="padding: 8px; border-bottom: 1px solid #eee; color: #d9534f; font-weight: bold;">₹ {rec.amount}</td></tr></table><div style="text-align: center; margin-top: 30px;"><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; font-size: 16px; font-weight: bold; display: inline-block;">Review &amp; Action Voucher</a></div></div>""",
                        })
                if task_vals_list:
                    self.env['project.task'].sudo().create(task_vals_list)
                if mail_vals_list:
                    self.env['mail.mail'].sudo().create(mail_vals_list).send()

    def action_approve_system_voucher(self):
        """Tier 1 Custodian authorization strictly limited to system-verified matrix payouts."""
        for rec in self:
            if not rec.is_system_generated:
                raise ValidationError(
                    _("Security Exception: Tier 1 Custodians can only approve system-generated vouchers. Manual vouchers require Manager approval."))
            # Bypass the standard manager routing and auto-approve
            rec.action_approve()

    @api.depends('clinic_id', 'date')
    def _compute_allowed_therapists(self):
        """
        Daily Roster Scanner: Dynamically fetches therapists allowed for this clinic today.
        Combines statically assigned therapists + floaters scheduled for sessions today.
        """
        from datetime import datetime, time  # Added to safely evaluate matrix time boundaries

        for rec in self:
            if not rec.clinic_id:
                rec.allowed_therapist_ids = False
                continue

            Therapist = self.env['clinic.therapist'].sudo()
            allowed_ids = set()

            # 1. Fetch Therapists scheduled for sessions at this clinic ON THIS EXACT DATE (Legacy)
            if 'patient.session' in self.env and rec.date:
                sessions = self.env['patient.session'].sudo().search([
                    ('session_date', '=', rec.date),
                    '|',
                    ('therapy_clinic_id', '=', rec.clinic_id.id),
                    ('patient_id.clinic_id', '=', rec.clinic_id.id)
                ])
                allowed_ids.update(sessions.mapped('therapist_id').ids)

            # 1.5 FIX: Fetch Therapists with scheduled slots on the New Matrix Board today
            if 'clinic.schedule.appointment' in self.env and rec.date:
                start_day = datetime.combine(rec.date, time.min)
                end_day = datetime.combine(rec.date, time.max)
                matrix_apps = self.env['clinic.schedule.appointment'].sudo().search([
                    ('clinic_id', '=', rec.clinic_id.id),
                    ('start_datetime', '>=', start_day),
                    ('start_datetime', '<=', end_day)
                ])
                allowed_ids.update(matrix_apps.mapped('therapist_id').ids)

            # 2. Fetch Therapists statically assigned to this clinic (Schema Safe Fallback)
            m2m_field = None
            m2o_field = None
            for field_name, field_def in Therapist._fields.items():
                if getattr(field_def, 'comodel_name', '') == 'clinic.clinic':
                    if field_def.type == 'many2many':
                        m2m_field = field_name
                    elif field_def.type == 'many2one':
                        m2o_field = field_name
            target_field = m2m_field or m2o_field

            if target_field:
                if Therapist._fields[target_field].type == 'many2many':
                    static_therapists = Therapist.search([(target_field, 'in', rec.clinic_id.id)])
                else:
                    static_therapists = Therapist.search([(target_field, '=', rec.clinic_id.id)])
                allowed_ids.update(static_therapists.ids)

            # 3. Assign combined results. (Failsafe: If empty, allow all to prevent hard blocking)
            if allowed_ids:
                rec.allowed_therapist_ids = Therapist.browse(list(allowed_ids))
            else:
                rec.allowed_therapist_ids = Therapist.search([])

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

    @api.model
    def _get_user_clinic_ids(self, user=None):
        user = user or self.env.user
        clinic_ids = set()

        # 1. Check Primary Clinic
        if hasattr(user, 'clinic_id') and user.clinic_id:
            clinic_ids.add(user.clinic_id.id)

        # 2. CRITICAL FIX: Check Multiple Allowed Clinics Array
        if hasattr(user, 'clinic_ids') and user.clinic_ids:
            clinic_ids.update(user.clinic_ids.ids)

        # 3. Check Fund Manager Scopes
        if hasattr(user, 'op_fund_managed_clinic_ids') and user.op_fund_managed_clinic_ids:
            clinic_ids.update(user.op_fund_managed_clinic_ids.ids)
        if hasattr(user, 'op_fund_ho_managed_clinic_ids') and user.op_fund_ho_managed_clinic_ids:
            clinic_ids.update(user.op_fund_ho_managed_clinic_ids.ids)

        return list(clinic_ids)

    # NEUTRALIZED INTERCEPTORS - Allocations no longer block workflows

    def action_open_acknowledgment_wizard_from_banner(self):
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
                    role, ther = True, True
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
            rec.is_receipt_image = rec.receipt_filename.split('.')[-1].lower() in ['jpg', 'jpeg', 'png',
                                                                                   'webp'] if rec.receipt_filename else False

    @api.depends('signed_voucher_filename')
    def _compute_is_signed_voucher_image(self):
        for rec in self:
            rec.is_signed_voucher_image = rec.signed_voucher_filename.split('.')[-1].lower() in ['jpg', 'jpeg', 'png',
                                                                                                 'webp'] if rec.signed_voucher_filename else False

    @api.depends('payment_screenshot_filename')
    def _compute_is_payment_image(self):
        for rec in self:
            rec.is_payment_screenshot_image = rec.payment_screenshot_filename.split('.')[-1].lower() in ['jpg', 'jpeg',
                                                                                                         'png',
                                                                                                         'webp'] if rec.payment_screenshot_filename else False

    @api.depends('name')
    def _compute_s3_export_urls(self):
        for rec in self:
            rec.s3_receipt_url, rec.s3_voucher_url, rec.s3_payment_url = False, False, False
        if not self.ids or not boto3: return
        try:
            s3_client, bucket = self.env['ir.attachment']._get_s3_credentials()
            if not s3_client or not bucket: return

            attachments = self.env['ir.attachment'].sudo().search(
                [('res_model', '=', 'operational.fund.disbursement'), ('res_id', 'in', self.ids),
                 ('is_s3_stored', '=', True),
                 '|', ('res_field', '=', False), ('res_field', '!=', False)],
                order='id asc')
            att_map = {}
            for att in attachments:
                att_map.setdefault(att.res_id, []).append(att)
            for rec in self:
                for att in att_map.get(rec.id, []):
                    try:
                        # FIX: ResponseContentDisposition='inline' forces immediate preview
                        url = s3_client.generate_presigned_url(
                            'get_object',
                            Params={'Bucket': bucket, 'Key': att.s3_object_key, 'ResponseContentDisposition': 'inline'},
                            ExpiresIn=3600
                        )
                        if att.res_field == 'receipt_file':
                            rec.s3_receipt_url = url
                        elif att.res_field == 'signed_voucher_file':
                            rec.s3_voucher_url = url
                        elif att.res_field == 'payment_screenshot':
                            rec.s3_payment_url = url
                    except Exception:
                        pass
        except Exception:
            pass

    @api.onchange('home_visit_mrn_search', 'clinic_id', 'category', 'expense_category', 'travel_type', 'therapist_role')
    def _onchange_home_visit_mrn(self):
        if self.home_visit_mrn_search and self.show_home_visit:
            patient = self.env['clinic.patient'].sudo().search([('mrn', '=', self.home_visit_mrn_search)], limit=1)
            if patient:
                self.home_visit_patient_name, self.home_visit_patient_phone, self.home_visit_patient_clinic = patient.name, patient.phone, patient.clinic_id.name if patient.clinic_id else 'Unknown Clinic'
                self.is_cross_cluster_visit = self.clinic_id != patient.clinic_id if patient.clinic_id else True
            else:
                self.home_visit_patient_name, self.home_visit_patient_phone, self.home_visit_patient_clinic, self.is_cross_cluster_visit = False, False, False, False
                return {'warning': {'title': "Patient Not Found",
                                    'message': f"No patient found globally with MRN: {self.home_visit_mrn_search}"}}
        elif not self.show_home_visit:
            self.home_visit_mrn_search, self.home_visit_patient_name, self.home_visit_patient_phone, self.home_visit_patient_clinic, self.is_cross_cluster_visit = False, False, False, False, False

    @api.depends('category', 'expense_category', 'payee_type', 'vendor_name', 'therapist_name',
                 'therapist_role', 'travel_type', 'therapist_ref_id', 'vendor_ref_id')
    def _compute_payee_display(self):
        for rec in self:
            if rec.therapist_ref_id:
                rec.payee_display = rec.therapist_ref_id.name
            elif rec.vendor_ref_id:
                rec.payee_display = rec.vendor_ref_id.name
            elif rec.therapist_name:
                rec.payee_display = rec.therapist_name
            elif rec.vendor_name:
                rec.payee_display = rec.vendor_name
            else:
                rec.payee_display = 'Unknown Payee'

    def _route_for_approval(self):
        """
        DYNAMIC ROUTING ENGINE:
        Evaluates the voucher against 'operational.fund.approval.rule' domains.
        The first rule (by sequence) that matches the voucher dictates the outcome.
        """
        for rec in self:
            # 1. Fetch active routing rules ordered by sequence
            rules = self.env['operational.fund.approval.rule'].sudo().search([('active', '=', True)], order='sequence, id')
            matched_rule = False
            approvers = self.env['res.users']

            # 2. Evaluate domains against this specific voucher
            for rule in rules:
                domain = safe_eval(rule.domain or '[]')
                # If a domain is empty [], it acts as a global catch-all
                is_match = self.env['operational.fund.disbursement'].search_count([('id', '=', rec.id)] + domain) > 0
                if is_match:
                    matched_rule = rule
                    break

            # 3. Process the outcome of the matched rule
            if matched_rule:
                if matched_rule.action_type == 'block':
                    raise ValidationError(
                        _(matched_rule.block_message or "Submission rejected by system routing rules."))
                elif matched_rule.action_type == 'auto_approve':
                    rec.action_approve()
                    continue
                else:
                    approvers = matched_rule.approver_ids
            else:
                # Fallback if NO rules match
                approvers = self.env.ref('operational_fund.group_op_fund_manager').users
                if not approvers:
                    raise ValidationError(
                        _("System Architecture Error: No routing rule matched and no default managers were found in the system."))

            # 4. Generate Tasks and Emails for the assigned approvers
            base_url = rec.get_base_url()
            deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
            deadline = fields.Date.context_today(rec) + timedelta(days=1)
            task_vals_list, mail_vals_list = [], []

            for manager in approvers:
                rec.activity_schedule('mail.activity_data_todo', user_id=manager.id, summary='Review Voucher',
                                      note=f'Voucher Requires Approval. <a href="{deep_link}">Click here to view</a>')
                if 'project.task' in self.env:
                    task_vals_list.append({'name': f'Approve Voucher {rec.name}', 'user_ids': [(4, manager.id)],
                                           'date_deadline': deadline, 'is_voucher_task': True,
                                           'description': f'<p>Automated Routing: Requires your approval.</p><div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-primary" style="background-color: #00a09d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Review &amp; Action</a></div>'})
                if manager.email:
                    mail_vals_list.append({'subject': f'Action Required: Approve Voucher {rec.name}',
                                           'email_from': '<noreply@researchayu.com>', 'email_to': manager.email,
                                           'body_html': f'<div style="padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;"><h2 style="color: #333;">Voucher Approval Required</h2><p>Hello {escape(manager.name)}, a new operational fund disbursement ({rec.amount}) requires your review.</p><a href="{deep_link}" style="background-color: #00a09d; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px;">Review &amp; Action Voucher</a></div>'})

            if task_vals_list:
                self.env['project.task'].sudo().create(task_vals_list)
            if mail_vals_list:
                self.env['mail.mail'].sudo().create(mail_vals_list).send()

    def action_print_voucher(self):
        report = self.env.ref('operational_fund.action_report_op_fund_voucher', raise_if_not_found=False) or self.env[
            'ir.actions.report'].search([('report_name', '=', 'operational_fund.report_voucher_template')], limit=1)
        return report.report_action(self) if report else False

    def _cleanup_todo_tasks(self, task_name_prefix):
        if 'project.task' in self.env:
            for rec in self:
                tasks = self.env['project.task'].sudo().search([('name', '=', f'{task_name_prefix} {rec.name}')])
                if tasks: tasks.write({'active': False})

    def action_approve(self):
        mail_vals_list = []
        for rec in self:
            # 1. ADD VALIDATION HERE: Check for documents before allowing approval
            if rec.therapist_ref_id and not rec.signed_voucher_file:
                raise ValidationError(_("A Signed Voucher Asset is mandatory when a therapist is selected. Please upload it before approving."))
            if rec.vendor_ref_id and not rec.receipt_file:
                raise ValidationError(_("A Bill / Vendor Receipt is mandatory when a vendor is selected. Please upload it before approving."))

            # (Balance constraint removed. Funding is now strictly a visual ledger.)
            rec.state = 'approved'

            rec.approval_date = fields.Date.context_today(self)

            rec.activity_unlink(['mail.activity_data_todo'])
            self._cleanup_todo_tasks('Approve Voucher')

            if rec.create_uid and rec.create_uid.email:
                mail_vals_list.append(
                    {'subject': f'Approved: Voucher {rec.name}', 'email_from': '<noreply@researchayu.com>',
                     'email_to': rec.create_uid.email,
                     'body_html': f'<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #28a745;">Voucher Approved</h2><p>Hello,</p><p>Your voucher <strong>{escape(rec.name)}</strong> for {rec.amount} has been approved.</p></div>',
                     'state': 'outgoing'})

        if mail_vals_list: self.env['mail.mail'].sudo().create(mail_vals_list).send()

    def action_reject(self):
        self.ensure_one()
        return {'name': _('Reject Disbursement Voucher'), 'type': 'ir.actions.act_window',
                'res_model': 'operational.fund.rejection.wizard', 'view_mode': 'form', 'target': 'new',
                'context': {'default_disbursement_id': self.id}}

    def action_delete_draft(self):
        for rec in self:
            if rec.state not in ['draft', 'waiting']:
                if not self.env.user.has_group('operational_fund.group_op_fund_controller'):
                    raise ValidationError(_("Auditing Security: Only Tier 3 Controllers can delete vouchers that have already been approved or processed."))
        self.unlink()
        return {'type': 'ir.actions.act_window', 'name': 'Disbursements', 'res_model': 'operational.fund.disbursement',
                'view_mode': 'kanban,tree,form', 'target': 'current'}

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state in ('approved', 'paid'):
                raise ValidationError(
                    _("Auditing Restriction: Vouchers cannot be reset once they have been approved or paid."))
            elif rec.state == 'rejected':
                att = self.env['ir.attachment'].sudo().search([('res_model', '=', self._name), ('res_id', '=', rec.id),
                                                               ('res_field', '=', 'signed_voucher_file')], limit=1)
                if att: att.sudo().write({'res_field': 'old_signed_voucher_file'})
                rec.invalidate_recordset(['signed_voucher_file', 'old_signed_voucher_file'])
                rec.old_signed_voucher_filename = rec.signed_voucher_filename
                rec.signed_voucher_file, rec.signed_voucher_filename = False, False
            rec.state = 'waiting'

    def action_request_refund(self):
        for rec in self:
            if rec.state not in ('approved', 'paid'): raise ValidationError(
                _("Only authorized or paid vouchers can be submitted for a refund."))
            rec.state = 'refund_requested'
            active_clinic = rec.clinic_id
            managers = self.env.ref('operational_fund.group_op_fund_manager').users
            if managers:
                base_url = self.get_base_url()
                deep_link = f"{base_url}/web#id={rec.id}&model=operational.fund.disbursement&view_type=form"
                deadline = fields.Date.context_today(self) + timedelta(days=1)
                for manager in managers:
                    rec.activity_schedule('mail.activity_data_todo', user_id=manager.id,
                                          summary='Review Refund Request',
                                          note=f'A refund has been requested for Voucher {rec.name}.')
                    if 'project.task' in self.env:
                        self.env['project.task'].sudo().create(
                            {'name': f'Review Refund {rec.name}', 'user_ids': [(4, manager.id)],
                             'date_deadline': deadline, 'is_voucher_task': True,
                             'description': f'<p>A refund request for Voucher <strong>{rec.name}</strong> ({rec.amount}) requires your review.</p><br/><div contenteditable="false"><a href="{deep_link}" target="_blank" class="btn btn-warning" style="background-color: #f0ad4e; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; margin-top: 10px;">Click Here to Action Refund</a></div>'})

    # def action_approve_refund(self):
    #     mail_vals_list = []
    #     for rec in self:
    #         if rec.state != 'refund_requested': raise ValidationError(_("Refund must be requested first."))
    #         active_clinic = rec.clinic_id
    #         self.env['operational.fund.audit'].sudo().create(
    #             {'clinic_id': active_clinic.id, 'date': fields.Date.context_today(self), 'transaction_type': 'credit',
    #              'amount': rec.amount, 'reference': f'Refund: Fully Reclaimed Voucher {rec.name}',
    #              'user_id': self.env.user.id})
    #         rec.state = 'refunded'
    #         rec.activity_unlink(['mail.activity_data_todo'])
    #         self._cleanup_todo_tasks('Review Refund')
    #         active_clinic.sudo()._check_low_balance_alert()
    #         if rec.create_uid and rec.create_uid.email:
    #             mail_vals_list.append(
    #                 {'subject': f'Refund Approved: Voucher {rec.name}', 'email_from': '<noreply@researchayu.com>',
    #                  'email_to': rec.create_uid.email,
    #                  'body_html': f'<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #28a745;">Refund Approved</h2><p>Hello,</p><p>The refund for voucher <strong>{escape(rec.name)}</strong> has been approved.</p></div>',
    #                  'state': 'outgoing'})
    #     if mail_vals_list: self.env['mail.mail'].sudo().create(mail_vals_list).send()

    # def action_cancel_refund(self):
    #     mail_vals_list = []
    #     for rec in self:
    #         rec.state = 'approved'
    #         rec.activity_unlink(['mail.activity_data_todo'])
    #         self._cleanup_todo_tasks('Review Refund')
    #         if rec.create_uid and rec.create_uid.email:
    #             mail_vals_list.append(
    #                 {'subject': f'Refund Denied: Voucher {rec.name}', 'email_from': '<noreply@researchayu.com>',
    #                  'email_to': rec.create_uid.email,
    #                  'body_html': f'<div style="font-family: Arial, sans-serif; padding: 20px;"><h2 style="color: #d9534f;">Refund Denied</h2><p>Hello,</p><p>The refund request for voucher <strong>{escape(rec.name)}</strong> was denied.</p></div>',
    #                  'state': 'outgoing'})
    #     if mail_vals_list: self.env['mail.mail'].sudo().create(mail_vals_list).send()

    def action_bulk_download_assets(self):
        """
        Generates and directly downloads an executive XLSX audit manifest.
        - Ignores Odoo database "ghost" records by strictly validating against S3.
        - Primary documents get distinct, cleanly labeled URL columns.
        - Additional proofs dynamically expand into individual columns (Proof 1, Proof 2...).
        """
        if not self:
            return False

        s3_client, bucket = None, None
        if boto3:
            try:
                s3_client, bucket = self.env['ir.attachment']._get_s3_credentials()
            except Exception as e:
                _logger.error(f"AWS S3 credentials failed during Excel export: {e}")

        def get_s3_url(att):
            """Generates S3 Direct Object URLs. Returns False if it is an empty ghost record."""
            if not att:
                return False
            # Ensure it is a single record if passed a recordset
            if hasattr(att, 'ids'):
                att = att[0] if len(att) > 0 else False
            if not att:
                return False

            if not att.is_s3_stored:
                att._sync_to_s3()

            # If after syncing, it still has no S3 Key, it is a ghost record. Drop it.
                # If after syncing, it still has no S3 Key, it is a ghost record. Drop it.
            if s3_client and bucket and att.is_s3_stored and att.s3_object_key:
                try:
                    # FIX: Generate a secure presigned URL valid for 7 days
                    return s3_client.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': bucket, 'Key': att.s3_object_key},
                        ExpiresIn=604800
                    )
                except Exception as ex:
                    _logger.error(f"Object URL generation failed for key {att.s3_object_key}: {ex}")
            return False

        voucher_rows = []
        max_additional_proofs = 0
        IGNORED_PREFIXES = ('audit_manifest', 'disbursement_audit_manifest')

        for rec in self:
            # 1. AUTO-RESCUE LEGACY BINARY DATA (Safety Net)
            for field_name, file_name_field in [('signed_voucher_file', 'signed_voucher_filename'),
                                                ('receipt_file', 'receipt_filename'),
                                                ('payment_screenshot', 'payment_screenshot_filename')]:
                raw_data = getattr(rec, field_name)
                if raw_data:
                    # Look for an existing VALID attachment
                    existing_att = self.env['ir.attachment'].sudo().search([
                        ('res_model', '=', 'operational.fund.disbursement'),
                        ('res_id', '=', rec.id),
                        ('res_field', '=', field_name),
                        '|', ('is_s3_stored', '=', True), ('file_size', '>', 0)
                    ], limit=1, order="id desc")

                    if not existing_att:
                        file_name = getattr(rec, file_name_field) or f"{field_name}_{rec.name.replace('/', '_')}.bin"
                        new_att = self.env['ir.attachment'].sudo().create({
                            'name': file_name,
                            'type': 'binary',
                            'datas': raw_data,
                            'res_model': 'operational.fund.disbursement',
                            'res_id': rec.id,
                            'res_field': field_name,
                        })
                        new_att._sync_to_s3()

            # 2. Gather All Attachments
            #
            # ROOT-CAUSE FIX: Odoo's ir.attachment._search() silently prepends
            # ('res_field', '=', False) to any domain that does not mention
            # 'id' or 'res_field'. Signed Voucher, Receipt and Payment Proof are
            # Binary(attachment=True) fields, so their attachments ALWAYS have
            # res_field set and were being filtered out here - only the plain
            # Many2many "Additional Proofs" came back. Mentioning res_field
            # (both branches of an OR) disables that implicit filter. This is
            # the same idiom Odoo core uses in ir_attachment.py. It works on
            # Odoo 15-18 (skip_res_field_check only exists from 17 onwards).
            direct_atts = self.env['ir.attachment'].sudo().search([
                ('res_model', '=', 'operational.fund.disbursement'),
                ('res_id', '=', rec.id),
                '|', ('res_field', '=', False), ('res_field', '!=', False),
            ], order="id desc")

            all_atts = (direct_atts | rec.proof_attachment_ids).exists()
            valid_atts = all_atts.filtered(
                lambda a: not (a.name and a.name.lower().startswith(IGNORED_PREFIXES))
            ).sorted('id', reverse=True)

            # 3. Cascading Document Matcher (Filters out Ghost Records automatically)
            def extract_primary_url(res_field, s3_key, name_keywords):
                """Tries to find the file using database links first, falling back to name/s3 matches."""
                # Step A: Try exact database field mapping
                for att in valid_atts.filtered(lambda a: a.res_field == res_field):
                    url = get_s3_url(att)
                    if url: return url, att

                # Step B: Try strict AWS S3 Key signature match
                for att in valid_atts.filtered(lambda a: a.s3_object_key and s3_key in a.s3_object_key):
                    url = get_s3_url(att)
                    if url: return url, att

                # Step C: Try loose text fallback (If uploaded manually by user)
                for kw in name_keywords:
                    for att in valid_atts.filtered(lambda a: a.name and kw in a.name.lower()):
                        url = get_s3_url(att)
                        if url: return url, att

                return False, False

            # Extract URLs & Record matched attachment IDs to exclude them from the general "Additional Proofs"
            voucher_url, v_att = extract_primary_url('signed_voucher_file', 'Signed_Voucher',
                                                     ['signed_voucher', 'voucher'])
            receipt_url, r_att = extract_primary_url('receipt_file', '_Receipt_', ['receipt', 'bill'])
            payment_url, p_att = extract_primary_url('payment_screenshot', 'Payment_Proof',
                                                     ['payment_proof', 'payment'])

            primary_ids = [att.id for att in (v_att, r_att, p_att) if att]

            # 4. Filter the remaining legitimate URLs into Supporting Proofs
            supporting_proofs = []
            for att in valid_atts:
                if att.id not in primary_ids:
                    url = get_s3_url(att)
                    if url:  # Only add to Excel if it's a real, accessible AWS URL
                        supporting_proofs.append(url)

            # Track column expansion bounds
            if len(supporting_proofs) > max_additional_proofs:
                max_additional_proofs = len(supporting_proofs)

            voucher_rows.append({
                'rec': rec,
                'voucher_url': voucher_url,
                'receipt_url': receipt_url,
                'payment_url': payment_url,
                'supporting_urls': supporting_proofs,
            })

        # 5. Build XLSX in memory
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('Audit Manifest')

        # Format Profiles
        header_format = workbook.add_format({
            'bold': True, 'bg_color': '#1F4E78', 'font_color': '#FFFFFF',
            'align': 'center', 'valign': 'vcenter', 'border': 1, 'font_size': 11
        })
        text_format = workbook.add_format({'valign': 'vcenter', 'border': 1, 'font_size': 10})
        text_center = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1, 'font_size': 10})
        account_format = workbook.add_format({'num_format': '@', 'valign': 'vcenter', 'border': 1, 'font_size': 10})
        amount_format = workbook.add_format(
            {'num_format': ' #,##0.00', 'valign': 'vcenter', 'border': 1, 'font_size': 10})

        # Clean hyperlink styling
        link_format = workbook.add_format({
            'font_color': '#0563C1', 'underline': 1, 'valign': 'vcenter', 'border': 1, 'font_size': 10
        })
        na_format = workbook.add_format({
            'font_color': '#888888', 'align': 'center', 'valign': 'vcenter', 'border': 1, 'font_size': 10,
            'bg_color': '#F9F9F9'
        })

        # Generate Dynamic Column Headers
        headers = [
            'Voucher Number', 'Date', 'Clinic Branch', 'Amount', 'Status',
            'Payee Name', 'Bank Name', 'Account Number', 'IFSC Code',
            'Signed Voucher Asset', 'Vendor Receipt / Bill', 'Payment Proof'
        ]

        # Extends horizontally based on the highest amount of additional proofs found
        for i in range(1, max_additional_proofs + 1):
            headers.append(f"Additional Proof {i}")

        headers.append('Debit Narr')

        worksheet.freeze_panes(1, 0)
        worksheet.set_row(0, 26)
        for col_idx, header in enumerate(headers):
            worksheet.write(0, col_idx, header, header_format)

        # 6. Populate Data Rows
        for row_idx, item in enumerate(voucher_rows, start=1):
            rec = item['rec']

            payee_name = rec.payee_display or 'N/A'
            bank_name = rec.bank_name or False
            acc_num = rec.bank_account_number or False
            ifsc = rec.bank_ifsc_code or False

            if not bank_name and rec.vendor_ref_id:
                bank_name = rec.vendor_ref_id.bank_account_name or False
                acc_num = acc_num or rec.vendor_ref_id.bank_account_number or False
                ifsc = ifsc or rec.vendor_ref_id.bank_ifsc_code or False
            elif not bank_name and rec.therapist_ref_id:
                bank_name = getattr(rec.therapist_ref_id, 'bank_name', False) or getattr(rec.therapist_ref_id,
                                                                                         'bank_account_name', False)
                acc_num = acc_num or getattr(rec.therapist_ref_id, 'bank_account_number', False)
                ifsc = ifsc or getattr(rec.therapist_ref_id, 'bank_ifsc_code', False)

            worksheet.set_row(row_idx, 22)  # Enforce clean, uniform row height

            worksheet.write_string(row_idx, 0, str(rec.name or ''), text_format)
            worksheet.write_string(row_idx, 1, str(rec.date or ''), text_center)
            worksheet.write_string(row_idx, 2, str(rec.clinic_id.name if rec.clinic_id else 'Unknown Branch'),
                                   text_format)
            worksheet.write_number(row_idx, 3, rec.amount, amount_format)
            worksheet.write_string(row_idx, 4, str(rec.state or 'waiting').upper(), text_center)
            worksheet.write_string(row_idx, 5, str(payee_name), text_format)
            worksheet.write_string(row_idx, 6, str(bank_name).strip() if bank_name else 'N/A', text_format)
            worksheet.write_string(row_idx, 7, str(acc_num).strip() if acc_num else 'N/A', account_format)
            worksheet.write_string(row_idx, 8, str(ifsc).strip() if ifsc else 'N/A', text_center)

            def write_clean_hyperlink(row, col, url, label):
                """Hides the messy URL behind a neat clickable label"""
                if url:
                    worksheet.write_url(row, col, url, link_format, string=label)
                else:
                    worksheet.write_string(row, col, 'N/A', na_format)

            # Assign Primary Documentation Columns
            write_clean_hyperlink(row_idx, 9, item['voucher_url'], 'View Voucher')
            write_clean_hyperlink(row_idx, 10, item['receipt_url'], 'View Receipt')
            write_clean_hyperlink(row_idx, 11, item['payment_url'], 'View Payment')

            # Dynamically push additional proofs horizontally across generated columns
            col_idx = 12
            for i in range(max_additional_proofs):
                if i < len(item['supporting_urls']):
                    write_clean_hyperlink(row_idx, col_idx, item['supporting_urls'][i], f'View Proof {i + 1}')
                else:
                    worksheet.write_string(row_idx, col_idx, 'N/A', na_format)
                col_idx += 1

            # Debit Narr cleanly caps off the row
            worksheet.write_string(row_idx, col_idx, str(rec.debit_narr or ''), text_format)

        # 7. Styling Width Adjustments
        worksheet.set_column(0, 0, 26)
        worksheet.set_column(1, 1, 13)
        worksheet.set_column(2, 2, 34)
        worksheet.set_column(3, 3, 14)
        worksheet.set_column(4, 4, 14)
        worksheet.set_column(5, 5, 20)
        worksheet.set_column(6, 6, 18)
        worksheet.set_column(7, 7, 22)
        worksheet.set_column(8, 8, 15)

        worksheet.set_column(9, 11, 22)  # Primary Document URLs

        # Apply strict column widths to the mathematically generated extra columns
        if max_additional_proofs > 0:
            worksheet.set_column(12, 11 + max_additional_proofs, 20)

        # Format Debit Narr column at the very end
        worksheet.set_column(12 + max_additional_proofs, 12 + max_additional_proofs, 25)

        workbook.close()
        output.seek(0)
        xlsx_data = output.getvalue()

        filename = f"Disbursement_Audit_Manifest_{fields.Date.context_today(self).strftime('%Y%m%d')}.xlsx"

        self.env['ir.attachment'].sudo().search([
            ('name', '=', filename), ('res_model', '=', 'operational.fund.disbursement')
        ]).unlink()

        attachment = self.env['ir.attachment'].sudo().create({
            'name': filename,
            'type': 'binary',
            'raw': xlsx_data,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'public': False,
            'res_model': False,
            'res_id': 0
        })

        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self'
        }

    @api.constrains('amount')
    def _check_amount_validity(self):
        """Validation: Voucher amount cannot be empty or zero."""
        for rec in self:
            if rec.amount <= 0.0:
                raise ValidationError(
                    _("Amount must be greater than zero. Empty or negative amount vouchers cannot be created."))

    @api.constrains('date')
    def _check_voucher_date_is_today(self):
        """Validation: Vouchers can only be created for today."""
        for rec in self:
            if rec.date and rec.date != fields.Date.context_today(self):
                raise ValidationError(
                    _("Auditing Restriction: Vouchers can only be created for today's date. Yesterday or tomorrow is not allowed."))


    def unlink(self):
        for rec in self:
            if rec.state not in ['draft', 'waiting']:
                if not self.env.user.has_group('operational_fund.group_op_fund_controller'):
                    raise ValidationError(
                        _("Auditing Security: Only Tier 3 Controllers can delete vouchers that have already been approved or processed."))
        return super().unlink()

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))

    def write(self, vals):
        res = super().write(vals)
        if 'proof_attachment_ids' in vals:
            for rec in self:
                if rec.proof_attachment_ids:
                    rec.proof_attachment_ids.sudo().filtered(lambda a: not a.res_id).write({
                        'res_model': 'operational.fund.disbursement',
                        'res_id': rec.id,
                    })
        return res


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


class OperationalFundDownloadWizard(models.TransientModel):
    _name = 'operational.fund.download.wizard'
    _description = 'Download Daily Vouchers'

    date = fields.Date(string='Date', default=fields.Date.context_today, required=True)

    # New Field
    only_approved = fields.Boolean(string='Only Approved Vouchers', default=False,
                                   help="If checked, skips 'Waiting' vouchers and only downloads those explicitly approved.")

    include_paid = fields.Boolean(string='Include Paid Vouchers', default=False,
                                  help="If checked, downloads vouchers that have already been marked as paid.")

    def action_download_vouchers(self):
        self.ensure_one()

        # 1. Base states
        target_states = ['approved'] if self.only_approved else ['waiting', 'approved']
        if self.include_paid:
            target_states.append('paid')

        # 2. Dynamic Search Domain
        if self.only_approved or self.include_paid:
            domain = [
                '|',  # Odoo OR operator
                '&',  # Odoo AND operator for the fallback
                ('approval_date', '=', False),  # If it's an old voucher...
                ('date', '=', self.date),  # ...use its creation date.
                ('approval_date', '=', self.date),  # Standard check for newly approved vouchers.
                ('state', 'in', target_states),
            ]
        else:
            domain = [
                '|',
                ('date', '=', self.date),
                ('approval_date', '=', self.date),
                ('state', 'in', target_states)
            ]

        # 3. Execute Search
        vouchers = self.env['operational.fund.disbursement'].search(domain)

        if not vouchers:
            raise ValidationError(f"No vouchers found for {self.date} matching the selected criteria.")

        return vouchers.action_bulk_download_assets()


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    is_s3_stored = fields.Boolean(string="Stored in AWS S3", default=False, index=True)
    s3_object_key = fields.Char(string="AWS S3 Object Key")

    @api.model
    def _get_s3_credentials(self):
        if boto3 is None:
            _logger.error("System Architecture Error: Python 'boto3' library is missing.")
            return None, None

        ICP = self.env['ir.config_parameter'].sudo()
        bucket = config.get('op_fund_s3_bucket') or os.environ.get('AWS_S3_BUCKET') or ICP.get_param(
            'operational_fund.s3_bucket')
        access_key = config.get('op_fund_s3_access_key') or os.environ.get('AWS_ACCESS_KEY_ID') or ICP.get_param(
            'operational_fund.s3_access_key')
        secret_key = config.get('op_fund_s3_secret_key') or os.environ.get('AWS_SECRET_ACCESS_KEY') or ICP.get_param(
            'operational_fund.s3_secret_key')
        region = config.get('op_fund_s3_region') or os.environ.get('AWS_DEFAULT_REGION') or ICP.get_param(
            'operational_fund.s3_region', 'ap-south-1')
        custom_endpoint = config.get('op_fund_s3_endpoint_url') or os.environ.get('AWS_S3_ENDPOINT_URL')

        if not bucket:
            _logger.warning("AWS S3 Warning: S3 Bucket Name is not configured.")
            return None, None
        try:
            client_kwargs = {'region_name': (region or 'ap-south-1').strip()}
            if access_key and secret_key:
                client_kwargs['aws_access_key_id'] = access_key.strip()
                client_kwargs['aws_secret_access_key'] = secret_key.strip()
            if custom_endpoint:
                client_kwargs['endpoint_url'] = custom_endpoint.strip()
            return boto3.client('s3', **client_kwargs), bucket.strip()
        except Exception as e:
            _logger.error(f"AWS S3 Client Initialization Failed: {str(e)}")
            return None, None

    def _generate_s3_object_key(self):
        """Builds a human-readable, unique S3 key using the voucher/deposit number."""
        self.ensure_one()
        voucher_folder = 'Unassigned'
        if self.res_model == 'operational.fund.disbursement' and self.res_id:
            disb = self.env['operational.fund.disbursement'].browse(self.res_id)
            if disb.exists() and disb.name:
                voucher_folder = disb.name.replace('/', '_').strip()
        elif self.res_model == 'operational.fund.allocation' and self.res_id:
            alloc = self.env['operational.fund.allocation'].browse(self.res_id)
            if alloc.exists() and alloc.name:
                voucher_folder = alloc.name.replace('/', '_').strip()

        tag_map = {
            'receipt_file': 'Receipt',
            'signed_voucher_file': 'Signed_Voucher',
            'payment_screenshot': 'Payment_Proof',
            'ack_proof_file': 'Bank_Ack_Proof',
            'old_signed_voucher_file': 'Archived_Voucher',
        }
        doc_type = tag_map.get(self.res_field)
        if not doc_type:
            doc_type = f"Proof_{self.name.split('.')[0].replace(' ', '_')}" if self.name else 'Attachment'

        safe_mimetype = self.mimetype or 'application/octet-stream'
        file_ext = mimetypes.guess_extension(safe_mimetype) or ''
        if self.name and '.' in self.name:
            file_ext = f".{self.name.split('.')[-1].lower()}"
        if not file_ext:
            file_ext = '.bin'
        return f"operational_funds/{voucher_folder}/{voucher_folder}_{doc_type}_{self.id}{file_ext}"

    def _sync_to_s3(self):
        """Uploads pending binary attachments to AWS S3 under their voucher folder."""
        if not boto3:
            return

        target_models = ['operational.fund.disbursement', 'operational.fund.allocation']
        valid_recs = self.filtered(
            lambda r: r.res_model in target_models and r.res_id and r.type == 'binary' and not r.is_s3_stored)

        if not valid_recs:
            return
        s3_client, bucket = self._get_s3_credentials()
        if not s3_client or not bucket:
            return

        for rec in valid_recs:
            payload = rec.with_context(bin_size=False).raw
            if not payload and rec.datas:
                payload = base64.b64decode(rec.datas)
            if not payload:
                continue

            try:
                safe_mimetype = rec.mimetype or 'application/octet-stream'
                object_key = rec._generate_s3_object_key()

                s3_client.put_object(
                    Bucket=bucket,
                    Key=object_key,
                    Body=payload,
                    ContentType=safe_mimetype,
                    ContentDisposition='inline'
                )

                actual_size = len(payload)

                # 1. Purge the physical file from the local hard drive to save disk space
                if rec.store_fname:
                    try:
                        full_path = rec._full_path(rec.store_fname)
                        if os.path.exists(full_path):
                            os.unlink(full_path)
                    except Exception as e:
                        _logger.warning(f"Could not delete local file: {e}")

                # 2. THE UI ANCHOR: We inject a tiny dummy string into db_datas.
                # This guarantees Odoo's Form View sees the field as "Not Empty" and successfully loads the Image/PDF widgets.
                dummy_payload = base64.b64encode(b'S3_UI_ANCHOR')

                self.env.cr.execute("""
                    UPDATE ir_attachment 
                    SET db_datas = %s, 
                        store_fname = NULL, 
                        is_s3_stored = TRUE, 
                        s3_object_key = %s,
                        file_size = %s
                    WHERE id = %s
                """, (dummy_payload, object_key, actual_size, rec.id))

                rec.invalidate_recordset(['db_datas', 'store_fname', 'is_s3_stored', 's3_object_key', 'file_size'])

            except Exception as e:
                _logger.error(f"S3 Upload failed for attachment {rec.id}: {str(e)}")

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_to_s3()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ('raw', 'datas', 'res_id', 'res_model', 'res_field')):
            self._sync_to_s3()
        return res

    @api.depends('store_fname', 'db_datas', 'file_size')
    def _compute_raw(self):
        super()._compute_raw()
        if not boto3:
            return

        s3_client, bucket = None, None
        for attach in self:
            if attach.is_s3_stored and attach.s3_object_key:
                if self.env.context.get('bin_size'):
                    continue
                # Failsafe: Only triggered if IrBinary interceptor is somehow completely bypassed
                if not s3_client:
                    s3_client, bucket = self._get_s3_credentials()
                if s3_client and bucket:
                    try:
                        s3_object = s3_client.get_object(Bucket=bucket, Key=attach.s3_object_key)
                        attach.raw = s3_object['Body'].read()
                    except Exception as e:
                        _logger.error(f"S3 Download fallback failed for {attach.s3_object_key}: {e}")

    def unlink(self):
        for attachment in self:
            if attachment.res_model == 'operational.fund.disbursement' and attachment.res_id:
                disb = self.env['operational.fund.disbursement'].browse(attachment.res_id)
                if disb.exists() and disb.state in ('approved', 'paid', 'refund_requested', 'refunded'):
                    if not self.env.su:
                        raise ValidationError(
                            _("Auditing Security: You cannot delete attachments from a finalized operational disbursement."))

        s3_targets = self.filtered(lambda a: a.is_s3_stored and a.s3_object_key)
        if boto3 and s3_targets:
            try:
                s3_client, bucket = self._get_s3_credentials()
                if s3_client and bucket:
                    for attachment in s3_targets:
                        try:
                            s3_client.delete_object(Bucket=bucket, Key=attachment.s3_object_key)
                        except Exception as e:
                            _logger.error(f"Failed to delete orphaned S3 object {attachment.s3_object_key}: {e}")
            except Exception as outer_e:
                _logger.error(f"Could not connect to S3 to delete object: {outer_e}")
        return super().unlink()


# ===========================================================================
# THE NATIVE STREAM INTERCEPTOR
# ===========================================================================
from odoo.http import request, Stream


class IrBinary(models.AbstractModel):
    _inherit = 'ir.binary'

    @api.model
    def _get_stream_from(self, record, field_name='raw', *args, **kwargs):
        """
        Intercepts ALL native Odoo widgets (pdf_viewer, image, download) safely.
        """
        attachment = False

        # Target 1: M2M Additional Proofs requests
        if record._name == 'ir.attachment':
            attachment = record
        # Target 2: Binary field requests on the Disbursement model itself (Vendor Receipt, Voucher)
        elif field_name and field_name != 'raw':
            attachment = self.env['ir.attachment'].sudo().search([
                ('res_model', '=', record._name),
                ('res_id', '=', record.id),
                ('res_field', '=', field_name)
            ], limit=1)

        if attachment and getattr(attachment, 'is_s3_stored', False) and getattr(attachment, 's3_object_key', False):
            s3_client, bucket = attachment._get_s3_credentials()
            if s3_client and bucket:
                try:
                    # NATIVE STREAMING: Fetch the bytes directly into the server.
                    # This circumvents all Browser CORS policies and redirect vulnerabilities.
                    s3_object = s3_client.get_object(Bucket=bucket, Key=attachment.s3_object_key)
                    raw_data = s3_object['Body'].read()

                    safe_mimetype = attachment.mimetype or 'application/octet-stream'

                    dl_filename = attachment.name or 'document'
                    if args and args[0]:
                        dl_filename = args[0]
                    elif kwargs.get('filename'):
                        dl_filename = kwargs.get('filename')

                    return Stream(
                        type='data',
                        data=raw_data,
                        mimetype=safe_mimetype,
                        download_name=str(dl_filename).replace('"', '')
                    )
                except Exception as e:
                    _logger.error(f"S3 Native Stream Error: {e}")

        # If not an S3 attachment, let Odoo load natively
        return super()._get_stream_from(record, field_name, *args, **kwargs)


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


class OperationalFundUtrWizard(models.TransientModel):
    _name = 'operational.fund.utr.wizard'
    _description = 'Batch UTR Upload Wizard'

    csv_file = fields.Binary(string='Bank Payment Sheet (CSV / XLSX)', required=True)
    file_name = fields.Char(string='File Name')

    def action_process_csv(self):
        self.ensure_one()
        if not self.csv_file:
            raise ValidationError(_("Please upload a file."))

        raw_data = base64.b64decode(self.csv_file)
        file_name = (self.file_name or '').lower()
        rows = []

        # 1. HANDLE NATIVE EXCEL (XLSX) FILES
        if file_name.endswith('.xlsx'):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(filename=io.BytesIO(raw_data), data_only=True)
                ws = wb.active
                sheet_data = list(ws.iter_rows(values_only=True))

                if not sheet_data:
                    raise ValidationError(_("The uploaded Excel file is empty."))

                headers = [str(h or '').strip() for h in sheet_data[0]]
                for row_data in sheet_data[1:]:
                    row_dict = {}
                    for idx, cell_val in enumerate(row_data):
                        if idx < len(headers):
                            row_dict[headers[idx]] = str(cell_val).strip() if cell_val is not None else ''
                    rows.append(row_dict)
            except ImportError:
                raise ValidationError(
                    _("The system requires the 'openpyxl' Python library to read Excel files. Please upload a standard CSV instead."))
            except Exception as e:
                raise ValidationError(_(f"Failed to read the Excel file: {e}"))

        # 2. HANDLE STANDARD CSV FILES
        else:
            try:
                decoded_file = raw_data.decode('utf-8-sig')
            except UnicodeDecodeError:
                decoded_file = raw_data.decode('latin1')

            try:
                # newline='' strictly prevents CSV module from crashing on embedded carriage returns
                reader = csv.DictReader(io.StringIO(decoded_file, newline=''))
                rows = list(reader)
            except csv.Error as e:
                raise ValidationError(
                    _(f"CSV Parsing Error: {e}. Please ensure you uploaded a valid text CSV, or try uploading an Excel (.xlsx) file instead."))

        success_count = 0
        skipped_count = 0
        mail_vals_list = []

        for row in rows:
            # Flexible dictionary key matching to prevent strict casing errors
            row_keys = {k.strip().lower(): k for k in row.keys() if k}

            # Dynamically identify the Voucher and UTR columns
            v_key = next((row_keys[k] for k in row_keys if 'voucher' in k or 'name' in k or 'code' in k), None)
            u_key = next((row_keys[k] for k in row_keys if 'utr' in k or 'ref' in k), None)
            d_key = next((row_keys[k] for k in row_keys if 'debit' in k or 'narr' in k or 'remark' in k), None)

            if not v_key or not u_key:
                raise ValidationError(
                    _("Invalid File Format. The system could not detect columns for 'Voucher' and 'UTR'. Check your headers."))

            voucher_code = str(row.get(v_key, '')).strip()
            utr_number = str(row.get(u_key, '')).strip()
            debit_narr_val = str(row.get(d_key, '')).strip() if d_key else ''

            if not voucher_code or not utr_number:
                continue

            # Lock onto the exact record
            voucher = self.env['operational.fund.disbursement'].search([('name', '=', voucher_code)], limit=1)

            # SECURITY GUARD: Only process if structurally approved
            if voucher and voucher.state == 'approved':
                vals = {
                    'utr_reference': utr_number,
                    'state': 'paid',
                }
                if debit_narr_val:
                    vals['debit_narr'] = debit_narr_val

                voucher.write(vals)
                success_count += 1

                # Batch email notification generation
                if voucher.create_uid and voucher.create_uid.email:
                    mail_vals_list.append({
                        'subject': f'Paid: Voucher {voucher.name}',
                        'email_from': '<noreply@researchayu.com>',
                        'email_to': voucher.create_uid.email,
                        'body_html': f"""<div style="font-family: Arial, sans-serif; padding: 20px;">
                                         <h2 style="color: #17a2b8;">Voucher Paid & Processed</h2>
                                         <p>Your voucher <strong>{escape(voucher.name)}</strong> has been finalized by the Accounts team.</p>
                                         <p><strong>Bank UTR Reference:</strong> {escape(utr_number)}</p>
                                         </div>""",
                        'state': 'outgoing',
                    })
            else:
                skipped_count += 1

        # Dispatch all finalized emails in a single database transaction
        if mail_vals_list:
            self.env['mail.mail'].sudo().create(mail_vals_list).send()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Batch Processing Complete'),
                'message': _('Successfully marked %s vouchers as Paid. Skipped %s invalid or unapproved rows.') % (
                    success_count, skipped_count),
                'sticky': False,
                'type': 'success'
            }
        }


class OperationalFundVendor(models.Model):
    _name = 'operational.fund.vendor'
    _description = 'Operational Fund Local Vendor'

    name = fields.Char(string='Vendor Name', required=True)
    clinic_ids = fields.Many2many('clinic.clinic', string='Allowed Clinics', help="Clinics where this vendor operates.")
    bank_name = fields.Char(string='Bank Name')
    bank_account_name = fields.Char(string='Bank Account Name')
    bank_account_number = fields.Char(string='Account Number')
    bank_ifsc_code = fields.Char(string='IFSC Code')
    active = fields.Boolean(default=True)

    # --- PASSBOOK LEDGER FIELDS ---
    voucher_count = fields.Integer(string='Vouchers', compute='_compute_voucher_stats')
    total_paid = fields.Float(string='Total Paid', compute='_compute_voucher_stats')
    total_unpaid = fields.Float(string='Total Unpaid', compute='_compute_voucher_stats')

    def _compute_voucher_stats(self):
        for vendor in self:
            vouchers = self.env['operational.fund.disbursement'].search([('vendor_ref_id', '=', vendor.id)])
            vendor.voucher_count = len(vouchers)
            vendor.total_paid = sum(vouchers.filtered(lambda v: v.state == 'paid').mapped('amount'))
            vendor.total_unpaid = sum(vouchers.filtered(lambda v: v.state in ['waiting', 'approved']).mapped('amount'))

    def action_open_passbook(self):
        self.ensure_one()
        return {
            'name': f'Passbook Ledger: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'operational.fund.disbursement',
            'view_mode': 'tree,form,pivot',
            'domain': [('vendor_ref_id', '=', self.id)],
            'context': {'default_vendor_ref_id': self.id, 'search_default_group_by_date': 1}
        }

class ClinicTherapist(models.Model):
    _inherit = 'clinic.therapist'

    # --- THERAPIST PASSBOOK EXTENSION ---
    op_voucher_count = fields.Integer(string='OP Vouchers', compute='_compute_op_voucher_stats')
    op_total_paid = fields.Float(string='OP Total Paid', compute='_compute_op_voucher_stats')
    op_total_unpaid = fields.Float(string='OP Pending', compute='_compute_op_voucher_stats')

    def _compute_op_voucher_stats(self):
        for therapist in self:
            vouchers = self.env['operational.fund.disbursement'].search([('therapist_ref_id', '=', therapist.id)])
            therapist.op_voucher_count = len(vouchers)
            therapist.op_total_paid = sum(vouchers.filtered(lambda v: v.state == 'paid').mapped('amount'))
            therapist.op_total_unpaid = sum(vouchers.filtered(lambda v: v.state in ['waiting', 'approved']).mapped('amount'))

    def action_open_op_passbook(self):
        self.ensure_one()
        return {
            'name': f'Operational Ledger: {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'operational.fund.disbursement',
            'view_mode': 'tree,form,pivot',
            'domain': [('therapist_ref_id', '=', self.id)],
            'context': {'default_therapist_ref_id': self.id, 'search_default_group_by_date': 1}
        }


class ResUsers(models.Model):
    _inherit = 'res.users'

    # 👉 ADD THIS TO COMPLETE THE INVERSE RELATIONSHIP:
    op_fund_managed_clinic_ids = fields.Many2many(
        comodel_name='clinic.clinic',
        relation='clinic_op_fund_manager_rel',
        column1='user_id',
        column2='clinic_id',
        string='Managed Clinics (Operational Funds)'
    )


class OperationalFundRejectionWizard(models.TransientModel):
    _name = 'operational.fund.rejection.wizard'
    _description = 'Disbursement Rejection Wizard'

    disbursement_id = fields.Many2one('operational.fund.disbursement', string='Disbursement', required=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_confirm_reject(self):
        # mail_vals_list = []
        for wiz in self:
            disb = wiz.disbursement_id

            # (Audit Ledger reversal logic safely removed)

            disb.message_post(
                body=Markup(
                    f"<div style='color: #d9534f; font-size: 14px;'><i class='fa fa-ban'></i> <strong>VOUCHER REJECTED</strong><br/><strong>Reason:</strong> {escape(wiz.reason)}</div>"),
                subtype_xmlid='mail.mt_note'
            )
            disb.state = 'rejected'
            disb.activity_unlink(['mail.activity_data_todo'])
            disb._cleanup_todo_tasks('Approve Voucher')
            disb._cleanup_todo_tasks('Review Refund')

            # --- EMAIL LOGIC COMMENTED OUT ---
            # if disb.create_uid and disb.create_uid.email:
            #     mail_vals_list.append({ ... })

        # if mail_vals_list:
        #     self.env['mail.mail'].sudo().create(mail_vals_list).send()


class OperationalFundMarkPaidWizard(models.TransientModel):
    _name = 'operational.fund.mark.paid.wizard'
    _description = 'Mark Voucher as Paid Wizard'

    disbursement_id = fields.Many2one('operational.fund.disbursement', required=True)
    who_paid = fields.Char(string='Who Paid? (Typable Name)')
    proof_attachment_ids = fields.Many2many('ir.attachment', string='Payment Proofs')

    def action_confirm_paid(self):
        self.ensure_one()
        disb = self.disbursement_id

        # CRITICAL FIX: Transfer ownership of attachments to prevent Odoo auto-deletion
        if self.proof_attachment_ids:
            self.proof_attachment_ids.write({
                'res_model': 'operational.fund.disbursement',
                'res_id': disb.id
            })
            # Use command (4, id) to append without deleting existing proofs
            disb.write({
                'proof_attachment_ids': [(4, att.id) for att in self.proof_attachment_ids]
            })

        disb.write({
            'state': 'paid',
            'who_paid': self.who_paid,
            'approval_date': fields.Date.context_today(self)
        })

        disb.message_post(
            body=Markup(
                f"<div style='color: #28a745;'><i class='fa fa-check-circle'></i> <strong>MARKED AS PAID</strong><br/><b>Paid By:</b> {escape(self.who_paid or 'Not Specified')}</div>")
        )