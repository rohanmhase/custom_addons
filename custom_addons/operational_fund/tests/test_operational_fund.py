import base64
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError

class TestOperationalFund(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super(TestOperationalFund, cls).setUpClass()
        # 1. Create a dummy clinic
        cls.clinic = cls.env['clinic.clinic'].create({
            'name': 'Test Clinic Alpha',
            'code': 'TCA',
            'address': '123 Test St',
            'phone': '9999999999',
            'op_fund_balance': 0.0,
        })
        
        # 2. Create Dummy Users (Custodian and Manager)
        cls.user_custodian = cls.env['res.users'].create({
            'name': 'Test Custodian',
            'login': 'test_custodian',
            'email': 'custodian@test.com',
            'clinic_ids': [(4, cls.clinic.id)],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('operational_fund.group_op_fund_custodian').id,
                cls.env.ref('clinic_management.group_clinic_administrator').id
            ])]
        })
        
        cls.user_manager = cls.env['res.users'].create({
            'name': 'Test Manager',
            'login': 'test_manager',
            'email': 'manager@test.com',
            'clinic_ids': [(4, cls.clinic.id)],
            'groups_id': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('operational_fund.group_op_fund_manager').id,
                cls.env.ref('clinic_management.group_clinic_administrator').id
            ])]
        })
        
        # Add Manager to the clinic's managers
        cls.clinic.op_fund_manager_ids = [(4, cls.user_manager.id)]

    def test_01_allocation_email_notification(self):
        """Test if creating a top-up triggers an email to the allocated_to_id"""
        # Clear existing mail queue for clean test
        self.env['mail.mail'].search([]).unlink()

        # Create Allocation
        allocation = self.env['operational.fund.allocation'].create({
            'clinic_id': self.clinic.id,
            'amount': 5000.0,
            'allocated_to_id': self.user_custodian.id,
            'state': 'pending',
        })

        # Check if email was created
        emails = self.env['mail.mail'].search([('email_to', '=', 'custodian@test.com')])
        self.assertTrue(emails, "Email should be generated for the allocated user.")
        self.assertIn('Direct Allocation', emails[0].subject, "Subject should mention Direct Allocation")

    def test_02_allocation_approval_flow(self):
        """Test if approving the allocation updates balances and sends an email"""
        allocation = self.env['operational.fund.allocation'].create({
            'clinic_id': self.clinic.id,
            'amount': 5000.0,
            'allocated_to_id': self.user_custodian.id,
            'state': 'pending',
            'ack_proof_file': base64.b64encode(b'dummy_file_data')
        })
        
        # Clear emails
        self.env['mail.mail'].search([]).unlink()

        # Manager approves
        allocation.with_user(self.user_manager).action_approve_allocation()

        # Check state and balance
        self.assertEqual(allocation.state, 'cleared', "Allocation should be cleared")
        
        # Check approval email
        emails = self.env['mail.mail'].search([('email_to', '=', 'custodian@test.com')])
        self.assertTrue(emails, "Approval email should be sent to the allocated user.")
        self.assertIn('Approved', emails[0].subject)

    def test_03_disbursement_email_and_order(self):
        """Test Voucher approval emails and ordering"""
        # Inject money first so we don't hit Insufficient Funds
        self.env['operational.fund.allocation'].create({
            'clinic_id': self.clinic.id,
            'amount': 10000.0,
            'state': 'cleared'
        })

        voucher1 = self.env['operational.fund.disbursement'].with_user(self.user_custodian).create({
            'clinic_id': self.clinic.id,
            'amount': 1000.0,
            'state': 'draft',
        })
        voucher2 = self.env['operational.fund.disbursement'].with_user(self.user_custodian).create({
            'clinic_id': self.clinic.id,
            'amount': 2000.0,
            'state': 'draft',
        })

        # Check default ordering (DESC)
        vouchers = self.env['operational.fund.disbursement'].search([('clinic_id', '=', self.clinic.id)])
        self.assertEqual(vouchers[0].id, voucher2.id, "Vouchers should be sorted by ID descending.")

        # Clear emails
        self.env['mail.mail'].search([]).unlink()

        # Manager Approves Voucher 1
        voucher1.with_user(self.user_manager).action_approve()

        # Check state and email
        self.assertEqual(voucher1.state, 'approved', "Voucher should be approved")
        emails = self.env['mail.mail'].search([('email_to', '=', 'custodian@test.com')])
        self.assertTrue(emails, "Approval email should be sent to voucher creator.")
        self.assertIn('Approved', emails[0].subject)

    def test_04_allocation_rejection_flow(self):
        """Test if rejecting an allocation resets state, clears proof, and sends rejection email"""
        allocation = self.env['operational.fund.allocation'].create({
            'clinic_id': self.clinic.id,
            'amount': 3000.0,
            'allocated_to_id': self.user_custodian.id,
            'state': 'pending',
            'ack_proof_file': base64.b64encode(b'bad_proof_data'),
            'ack_proof_filename': 'bad_proof.png'
        })
        self.env['mail.mail'].search([]).unlink()

        # Manager rejects
        allocation.with_user(self.user_manager).action_reject_allocation()

        self.assertEqual(allocation.state, 'pending', "Allocation state should revert to pending after rejection")
        self.assertFalse(allocation.ack_proof_file, "Proof file should be cleared on rejection")
        
        # Check rejection email
        emails = self.env['mail.mail'].search([('email_to', '=', 'custodian@test.com')])
        self.assertTrue(emails, "Rejection email should be sent to allocated user.")
        self.assertIn('Rejected', emails[0].subject)

    def test_05_disbursement_rejection_flow(self):
        """Test Voucher rejection flow and email notification"""
        voucher = self.env['operational.fund.disbursement'].with_user(self.user_custodian).create({
            'clinic_id': self.clinic.id,
            'amount': 500.0,
            'state': 'draft',
        })
        self.env['mail.mail'].search([]).unlink()

        # Manager rejects voucher
        voucher.with_user(self.user_manager).action_reject()

        self.assertEqual(voucher.state, 'rejected', "Voucher state should be rejected")
        emails = self.env['mail.mail'].search([('email_to', '=', 'custodian@test.com')])
        self.assertTrue(emails, "Rejection email should be sent to voucher creator.")
        self.assertIn('Rejected', emails[0].subject)

    def test_06_allocation_wizard_clinic_selection(self):
        """Test Pending HQ Deposit proof upload wizard with clinic selection"""
        allocation = self.env['operational.fund.allocation'].create({
            'clinic_id': self.clinic.id,
            'amount': 7500.0,
            'state': 'pending',
        })

        # Open wizard and select clinic & allocation
        wizard = self.env['operational.fund.allocation.wizard'].with_user(self.user_custodian).create({
            'clinic_id': self.clinic.id,
            'allocation_id': allocation.id,
            'proof_file': base64.b64encode(b'dummy_pdf_content'),
            'proof_filename': 'bank_receipt.pdf'
        })
        wizard.action_submit_proof()

        self.assertTrue(allocation.ack_proof_file, "Proof file should be copied from wizard to allocation")
        self.assertEqual(allocation.ack_proof_filename, 'bank_receipt.pdf')
        self.assertTrue(allocation.is_ack_proof_pdf, "Computed is_ack_proof_pdf should be True for PDF files")

    def test_07_wallet_balance_and_audit_ledger(self):
        """Test wallet balance computation and audit ledger entry creation"""
        initial_balance = self.clinic.op_fund_balance or 0.0

        # Clear an allocation
        allocation = self.env['operational.fund.allocation'].create({
            'clinic_id': self.clinic.id,
            'amount': 20000.0,
            'state': 'pending',
            'ack_proof_file': base64.b64encode(b'proof')
        })
        allocation.with_user(self.user_manager).action_approve_allocation()

        # Balance should increase
        self.assertEqual(self.clinic.op_fund_balance, initial_balance + 20000.0, "Balance should increase upon cleared deposit")

        # Verify Audit Ledger Recharge Record
        recharge_audit = self.env['operational.fund.audit'].search([
            ('clinic_id', '=', self.clinic.id),
            ('type', '=', 'recharge'),
            ('amount', '=', 20000.0)
        ])
        self.assertTrue(recharge_audit, "An audit record of type 'recharge' should be created.")

        # Approve a disbursement
        voucher = self.env['operational.fund.disbursement'].with_user(self.user_custodian).create({
            'clinic_id': self.clinic.id,
            'amount': 4000.0,
            'state': 'draft',
        })
        voucher.with_user(self.user_manager).action_approve()

        # Balance should decrease
        self.assertEqual(self.clinic.op_fund_balance, initial_balance + 16000.0, "Balance should decrease upon approved voucher")

        # Verify Audit Ledger Disbursement Record
        disb_audit = self.env['operational.fund.audit'].search([
            ('clinic_id', '=', self.clinic.id),
            ('type', '=', 'disbursement'),
            ('amount', '=', 4000.0)
        ])
        self.assertTrue(disb_audit, "An audit record of type 'disbursement' should be created.")

    def test_08_insufficient_funds_validation(self):
        """Test that voucher cannot be approved if wallet balance is exceeded"""
        current_balance = self.clinic.op_fund_balance or 0.0
        voucher = self.env['operational.fund.disbursement'].with_user(self.user_custodian).create({
            'clinic_id': self.clinic.id,
            'amount': current_balance + 50000.0,
            'state': 'draft',
        })

        with self.assertRaises(ValidationError, msg="Approving a voucher exceeding wallet balance must raise ValidationError"):
            voucher.with_user(self.user_manager).action_approve()

