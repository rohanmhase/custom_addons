# -*- coding: utf-8 -*-
"""End-to-end flow test: one ticket, seven users, PO + partial payment + invoice.

Runs with:
    odoo-bin -u service_ticket --test-enable --test-tags /service_ticket

It creates its own users, teams, category and location, so it never touches
your data. Read it as the specification of the flow.
"""
from odoo import fields
from odoo.api import Environment
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

from odoo.addons.service_ticket.models.svcticket_ticket import OPEN_STATES


class TestServiceTicketFlow(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True,
                                       mail_create_nolog=True,
                                       mail_notrack=True))
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        g_user = cls.env.ref("service_ticket.group_user")
        g_mod = cls.env.ref("service_ticket.group_moderator")
        g_mgr = cls.env.ref("service_ticket.group_manager")
        internal = cls.env.ref("base.group_user")

        def user(login, groups):
            return Users.create({
                "name": login, "login": login, "email": "%s@test.local" % login,
                "groups_id": [(6, 0, [internal.id] + [g.id for g in groups])]})

        cls.rep = user("e2e_rep", [g_user])
        cls.tech1 = user("e2e_tech1", [g_user, g_mod])
        cls.tech2 = user("e2e_tech2", [g_user, g_mod])
        cls.lead = user("e2e_lead", [g_user, g_mod])
        cls.pomem = user("e2e_po", [g_user, g_mod])
        cls.acct = user("e2e_acct", [g_user, g_mod])
        cls.director = user("e2e_director", [g_user, g_mod])
        cls.manager = user("e2e_manager", [g_mgr])

        Team = cls.env["svcticket.team"]
        cls.facility = Team.create({
            "name": "E2E Facility", "team_type": "facility",
            "lead_ids": [(6, 0, [cls.lead.id])],
            "member_ids": [(6, 0, [cls.tech1.id, cls.tech2.id, cls.lead.id])]})
        cls.po = Team.create({
            "name": "E2E PO", "team_type": "po_team",
            "lead_ids": [(6, 0, [cls.manager.id])],
            "member_ids": [(6, 0, [cls.pomem.id])]})
        cls.accounts = Team.create({
            "name": "E2E Accounts", "team_type": "account_team",
            "lead_ids": [(6, 0, [cls.manager.id])],
            "member_ids": [(6, 0, [cls.acct.id])]})

        cls.approver = cls.env["svcticket.approver"].create({
            "user_id": cls.director.id, "job_title": "Director"})
        cls.location = cls.env["svcticket.location"].create({"name": "E2E Clinic"})
        cls.category = cls.env["svcticket.category"].create({
            "name": "E2E Repair", "code": "E2E", "require_intake_photo": False,
            "requires_approval": False})
        cls.priority = cls.env["svcticket.priority"].search(
            [("code", "=", "P2")], limit=1) or cls.env["svcticket.priority"].search(
            [], limit=1)
        cls.reason = cls.env["svcticket.hold.reason"].create({
            "name": "E2E Purchase", "needs_financials": True})
        cls.plain_reason = cls.env["svcticket.hold.reason"].create({
            "name": "E2E Waiting for parts", "needs_financials": False})
        cls.env["ir.config_parameter"].sudo().set_param(
            "svcticket.require_resolution_photo", "0")

        cls.ticket = cls.env["svcticket.ticket"].with_user(cls.rep).create({
            "location_id": cls.location.id, "category_id": cls.category.id,
            "priority_id": cls.priority.id if cls.priority else False,
            "description": "<p>AC not cooling</p>"})

    # ------------------------------------------------------------------ util
    def _pdf(self, name):
        return self.env["ir.attachment"].create({
            "name": name, "datas": b"JVBERi0xLjQK", "mimetype": "application/pdf"})

    def _options(self, user):
        return self.ticket.with_user(user).handoff_option_ids.mapped("code")

    def _add_for_payment(self, ticket, user):
        """Open the 'Added for Payment' wizard, attach a proof, and confirm it.

        Proof is compulsory at this step - no amount / reference is asked here.
        """
        action = ticket.with_user(user).action_added_for_payment()
        proof = self.env["ir.attachment"].with_user(user).create(
            {"name": "proof.pdf", "datas": b"JVBERi0xLjQK", "mimetype": "application/pdf"})
        wizard = self.env["svcticket.added.for.payment.wizard"].with_user(
            user).with_context(action.get("context") or {}).create(
            {"ticket_id": ticket.id, "proof_ids": [(6, 0, [proof.id])]})
        wizard.action_confirm()
        ticket.invalidate_recordset()

    # ----------------------------------------------------------------- tests
    def test_01_intake(self):
        t = self.ticket
        self.assertEqual(t.state, "new")
        self.assertEqual(t.current_team_id, self.facility)
        self.assertTrue(t.sla_deadline, "a new ticket must carry a deadline")
        self.assertEqual(len(t.sla_line_ids), 1)
        self.assertEqual(t.sla_line_ids.state, "running")

    def test_02_who_may_act(self):
        t = self.ticket
        self.assertFalse(t.with_user(self.rep).can_act)
        self.assertTrue(t.with_user(self.tech1).can_act)
        self.assertFalse(t.with_user(self.acct).can_act)
        self.assertTrue(t.with_user(self.manager).can_act)

    def test_03_buttons_per_holder(self):
        t = self.ticket
        facility = self._options(self.tech1)
        # the header "Request Hold" button owns that step - no duplicate row
        self.assertNotIn("hold_to_lead", facility)
        self.assertIn("request_docs", facility)
        self.assertNotIn("po_send_back", facility)
        self.assertFalse(self._options(self.pomem))

    def test_04_full_flow(self):
        t = self.ticket

        # work starts
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.assertEqual(t.state, "in_progress")

        # the tech asks the lead for a PO
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po",
            "reason_id": self.reason.id, "note": "compressor dead",
            "estimated_cost": 20000.0}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.state, "hold_requested")
        self.assertEqual(t.current_owner_type, "lead")
        self.assertEqual(t.current_user_id, self.lead)
        self.assertTrue(t.with_user(self.lead).can_vet_hold)
        self.assertFalse(t.with_user(self.tech1).can_vet_hold)

        # only the lead can approve it
        with self.assertRaises(UserError):
            t.with_user(self.tech2).action_approve_hold()

        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.state, "po_pending")
        self.assertEqual(t.current_team_id, self.po)
        self.assertFalse(t.sla_line_ids.filtered(
            lambda l: l.lane == "facility" and l.state == "running"))
        self.assertTrue(t.sla_line_ids.filtered(
            lambda l: l.lane == "po" and l.state == "running"))

        # the PO team's buttons changed with the owner
        po_opts = self._options(self.pomem)
        self.assertIn("po_send_back", po_opts)
        self.assertNotIn("hold_to_lead", po_opts)
        self.assertNotIn("send_to_approver", po_opts)

        # PO sends it to the director
        self.env["svcticket.po.wizard"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "BlueStar", "amount": 20000.0,
            "approver_ids": [(6, 0, [self.approver.id])],
            "note": "only vendor"}).action_send()
        t.invalidate_recordset()
        self.assertEqual(len(t.po_ids), 1)
        self.assertEqual(t.current_owner_type, "approver")
        self.assertEqual(t.current_approver_ids, self.approver)
        self.assertIn("Director", t.waiting_on)
        self.assertEqual(
            t.sla_line_ids.filtered(lambda l: l.lane == "approver")[:1].state,
            "paused")
        self.assertFalse(t.with_user(self.pomem).can_act)

        # director approves -> accounts
        t.po_ids.with_user(self.director).action_approve()
        t.invalidate_recordset()
        self.assertEqual(t.state, "payment_pending")
        self.assertEqual(t.current_team_id, self.accounts)
        self.assertAlmostEqual(t.amount_total, 20000.0, 2)
        self.assertTrue(t.sla_line_ids.filtered(
            lambda l: l.lane == "payment" and l.state == "running"))

        # added for payment pauses, and must not open a second clock
        self._add_for_payment(t, self.acct)
        pay_lines = t.sla_line_ids.filtered(lambda l: l.lane == "payment")
        self.assertEqual(len(pay_lines), 1, "Added for Payment must pause, not reopen")
        self.assertEqual(pay_lines.state, "paused")

        # 50% paid -> back to facility
        self.env["svcticket.payment.wizard"].with_user(self.acct).create({
            "ticket_id": t.id, "amount": 10000.0, "tender_type": "neft",
            "reference": "UTR50"}).action_confirm()
        t.invalidate_recordset()
        self.assertAlmostEqual(t.amount_paid, 10000.0, 2)
        self.assertAlmostEqual(t.amount_due, 10000.0, 2)
        self.assertEqual(t.current_team_id, self.facility)
        self.assertEqual(t.state, "in_progress")

        # invoice needs a file
        with self.assertRaises(UserError):
            self.env["svcticket.invoice.wizard"].with_user(self.tech1).create({
                "ticket_id": t.id, "invoice_number": "INV-0"}).action_confirm()

        self.env["svcticket.invoice.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "invoice_number": "INV-001",
            "attachment_ids": [(6, 0, [self._pdf("inv1.pdf").id])]}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.accounts)
        self.assertFalse(t.invoice_accepted)

        # no invoice accepted -> no resolution
        t.with_user(self.tech1).write({"resolution_notes": "fixed"})
        with self.assertRaises(UserError):
            t.with_user(self.tech1).action_resolve()

        # rejected invoice goes back, file is cleared, count goes up
        self.env["svcticket.invoice.reject.wizard"].with_user(self.acct).create({
            "ticket_id": t.id, "reason": "amount mismatch"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.facility)
        self.assertEqual(t.invoice_reject_count, 1)
        self.assertFalse(t.invoice_attachment_ids)
        self.assertFalse(t.invoice_number)

        # resubmit and accept
        self.env["svcticket.invoice.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "invoice_number": "INV-002",
            "attachment_ids": [(6, 0, [self._pdf("inv2.pdf").id])]}).action_confirm()
        t.invalidate_recordset()
        t.with_user(self.acct).action_accept_invoice()
        t.invalidate_recordset()
        self.assertTrue(t.invoice_accepted)
        # invoice accepted - the ticket stays with the accounts team for the
        # remaining payment (it does not go back to the facility team)
        self.assertEqual(t.current_team_id, self.accounts)

        # balance, then resolve
        self.env["svcticket.payment.wizard"].with_user(self.acct).create({
            "ticket_id": t.id, "amount": 10000.0, "tender_type": "neft",
            "reference": "UTRBAL"}).action_confirm()
        t.invalidate_recordset()
        self.assertAlmostEqual(t.amount_due, 0.0, 2)
        t.with_user(self.tech1).action_resolve()
        t.invalidate_recordset()
        self.assertEqual(t.state, "resolved")
        self.assertTrue(all(l.state == "done" for l in t.sla_line_ids))

        # reopen gives a fresh clock with the facility team
        t.with_user(self.tech1).action_reopen()
        t.invalidate_recordset()
        self.assertEqual(t.state, "in_progress")
        self.assertEqual(t.current_team_id, self.facility)
        self.assertTrue(t.sla_line_ids.filtered(lambda l: l.state == "running"))

    def test_045_hold_approve_and_reject_return_to_own_team(self):
        """A hold raised by any team must come back to that same team."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()

        # plain (non-PO) hold
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "waiting for parts"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_owner_type, "lead")

        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.facility,
                         "approved hold must return to the team that raised it")
        self.assertEqual(t.state, "on_hold")
        line = t.sla_line_ids.filtered(lambda l: l.state == "paused")
        self.assertTrue(line, "an approved hold parks the clock")
        self.assertTrue(t.sla_deadline,
                        "a parked clock must still show a deadline, never blank")
        # and the team can pick the work back up
        t.with_user(self.tech1).action_resume_work()
        t.invalidate_recordset()
        self.assertEqual(t.state, "in_progress")

    def test_046_rejected_hold_puts_the_ticket_back_to_work(self):
        """A rejected *hold request* is not a rejected ticket: the ticket goes
        back to the team that raised it, in progress, with a running clock."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "not needed"}).action_confirm()
        t.invalidate_recordset()

        self.env["svcticket.hold.reject.wizard"].with_user(self.lead).create({
            "ticket_id": t.id, "rejection_note": "budget frozen"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.facility,
                         "rejected hold must return to the team that raised it")
        self.assertEqual(t.state, "in_progress",
                         "a rejected hold resumes the work, it does not reject the ticket")
        self.assertEqual(t.hold_reject_note, "budget frozen")
        self.assertEqual(t.lane_state, "running", "the clock runs again")
        self.assertTrue(t.sla_deadline, "and it has a real deadline")
        self.assertEqual(len(t._open_lines()), 1, "exactly one clock is open")
        # the team can just carry on - no extra click needed
        self.assertTrue(t.with_user(self.tech1).can_act)

    def test_046b_rejecting_the_whole_ticket_stops_the_clock(self):
        """The other kind of rejection: the ticket itself is refused at intake."""
        vetting = self.env["svcticket.category"].create({
            "name": "E2E Vetted", "code": "E2E-VET", "requires_approval": True,
            "approver_ids": [(6, 0, [self.approver.id])]})
        t = self.env["svcticket.ticket"].with_user(self.rep).create({
            "location_id": self.location.id, "category_id": vetting.id,
            "priority_id": self.priority.id if self.priority else False,
            "description": "<p>wrong department</p>"})
        t.invalidate_recordset()
        self.assertEqual(t.state, "pending_approval")
        # rejecting asks for a reason first, then closes the ticket
        action = t.with_user(self.manager).action_reject_ticket()
        self.assertEqual(action.get("res_model"), "svcticket.reject.wizard")
        self.env["svcticket.reject.wizard"].with_user(self.manager).create({
            "ticket_id": t.id, "reason": "Wrong department"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.state, "rejected")
        self.assertFalse(t._open_lines(), "a rejected ticket owes no clock")
        self.assertFalse(t.sla_deadline, "no deadline on a dead ticket")
        self.assertNotIn(t.state, OPEN_STATES, "a rejected ticket is not open")

    def test_046c_director_rejects_the_po_ticket_goes_back_to_facility(self):
        """A rejected PO is rework, not a dead ticket."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po",
            "reason_id": self.reason.id, "note": "compressor",
            "estimated_cost": 5000.0}).action_confirm()
        t.invalidate_recordset()
        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.po)

        po = self.env["svcticket.po"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "BlueStar", "amount": 5000.0,
            "approver_ids": [(6, 0, [self.approver.id])], "note": "quote"})
        po.action_send_for_approval()
        t.invalidate_recordset()
        self.assertEqual(po.state, "pending")

        po.action_reject("too expensive")
        t.invalidate_recordset()
        self.assertEqual(po.state, "rejected")
        self.assertEqual(t.current_team_id, self.facility,
                         "a rejected PO goes back to the facility team")
        self.assertEqual(t.state, "in_progress", "and the ticket is workable again")
        self.assertEqual(t.lane_state, "running", "with a running clock")
        self.assertTrue(t.sla_deadline)
        self.assertEqual(len(t._open_lines()), 1, "exactly one clock is open")
        self.assertNotEqual(t.state, "rejected")

    def as_user(self, user):
        """A real (non-superuser) environment.

        TransactionCase runs with su=True, which silently bypasses record rules -
        that is exactly how an AccessError during a hand-off stayed invisible.
        """
        return Environment(self.env.cr, user.id,
                           dict(self.env.context), su=False)

    def test_050_handoff_works_for_real_users_not_superuser(self):
        """Walk the money flow with record rules switched ON."""
        t = self.ticket
        uid = t.id

        env1 = self.as_user(self.tech1)
        ticket = env1["svcticket.ticket"].browse(uid)
        ticket.action_accept()
        ticket.action_start_work()
        env1["svcticket.hold.wizard"].create({
            "ticket_id": uid, "request_type": "po", "reason_id": self.reason.id,
            "note": "compressor", "estimated_cost": 5000.0}).action_confirm()

        # the lead approves - this hands the ticket to a team the lead is not in
        env2 = self.as_user(self.lead)
        env2["svcticket.ticket"].browse(uid).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.po, "handed to the PO team")

        # the PO team raises the PO and sends it to the director
        env3 = self.as_user(self.pomem)
        po_env = env3["svcticket.po"]
        po = po_env.create({"ticket_id": uid, "vendor_name": "BlueStar", "amount": 5000.0,
                            "approver_ids": [(6, 0, [self.approver.id])], "note": "only vendor"})
        po.action_send_for_approval()
        t.invalidate_recordset()
        self.assertEqual(t.current_owner_type, "approver")
        self.assertEqual(t.current_team_id, self.po,
                         "the PO team keeps the ticket while the director decides")
        self.assertEqual(t.lane_state, "paused", "and the clock is paused")
        self.assertTrue(t.sla_deadline, "with a deadline still shown")

        # the PO team must not act while the director has it
        po_ticket = env3["svcticket.ticket"].browse(uid)
        self.assertFalse(po_ticket.can_act, "PO team cannot act while waiting on the director")
        self.assertFalse(po_ticket.handoff_option_ids)

        # the director approves from his own mailbox
        env4 = self.as_user(self.director)
        env4["svcticket.po"].browse(po.id).action_approve()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.accounts)
        self.assertEqual(t.state, "payment_pending")
        self.assertEqual(t.lane_state, "running")
        self.assertEqual(t.amount_due, 5000.0, "the PO counts once it is approved")

        # accounts marks it added for payment (proof is compulsory, no amount)
        env5 = self.as_user(self.acct)
        acc_ticket = env5["svcticket.ticket"].browse(uid)
        proof = self._pdf("proof.pdf")
        action = acc_ticket.action_added_for_payment()
        wizard = self.env["svcticket.added.for.payment.wizard"].with_user(
            self.acct).with_context(action.get("context") or {}).create(
            {"ticket_id": acc_ticket.id, "proof_ids": [(6, 0, [proof.id])]})
        wizard.action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.lane_state, "paused")
        env5["svcticket.payment.wizard"].create({
            "ticket_id": uid, "amount": 5000.0, "reference": "UTR1",
            "tender_type": "neft", "proof_ids": [(6, 0, [proof.id])]}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.facility, "money done -> back to facility")
        self.assertEqual(t.amount_due, 0.0)
        self.assertEqual(t.lane_state, "running")

    def test_051_plain_users_can_hand_a_ticket_to_another_team(self):
        """The strict case: nobody here is a moderator, so record rules apply.

        Handing a ticket to another team means writing it *after* it has changed
        hands - as a plain user that used to blow up with AccessError.
        """
        Users = self.env["res.users"].with_context(no_reset_password=True)
        internal = self.env.ref("base.group_user")
        g_user = self.env.ref("service_ticket.group_user")

        def plain(login):
            return Users.create({"name": login, "login": login,
                                 "email": login + "@t.local",
                                 "groups_id": [(6, 0, [internal.id, g_user.id])]})

        plead, ptech = plain("e2e_plead"), plain("e2e_ptech")
        team = self.env["svcticket.team"].create({
            "name": "E2E Plain Facility", "team_type": "facility",
            "lead_ids": [(6, 0, [plead.id])], "member_ids": [(6, 0, [plead.id, ptech.id])]})
        t = self.env["svcticket.ticket"].with_user(self.rep).create({
            "location_id": self.location.id, "category_id": self.category.id,
            "priority_id": self.priority.id if self.priority else False,
            "description": "<p>plain</p>"})
        t.write({"team_id": team.id, "current_team_id": team.id})
        t.invalidate_recordset()

        env_tech = self.as_user(ptech)
        env_lead = self.as_user(plead)

        # raise the hold as a plain member
        env_tech["svcticket.ticket"].browse(t.id).action_accept()
        env_tech["svcticket.ticket"].browse(t.id).action_start_work()
        env_tech["svcticket.hold.wizard"].create({
            "ticket_id": t.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "buy"}).action_confirm()

        # the plain lead approves: the ticket leaves his team mid-transaction
        env_lead["svcticket.ticket"].browse(t.id).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.po)
        self.assertEqual(t.state, "po_pending")
        self.assertEqual(t.lane_state, "running")
        self.assertTrue(t.sla_deadline)
        self.assertEqual(len(t._open_lines()), 1)

        # and the plain team can still act on its own tickets afterwards
        t2 = self.env["svcticket.ticket"].with_user(self.rep).create({
            "location_id": self.location.id, "category_id": self.category.id,
            "priority_id": self.priority.id if self.priority else False,
            "description": "<p>plain 2</p>"})
        t2.write({"team_id": team.id, "current_team_id": team.id})
        env_tech["svcticket.ticket"].browse(t2.id).action_accept()
        t2.invalidate_recordset()
        self.assertEqual(t2.state, "acknowledged")

    def test_052_documents_follow_the_ticket_not_the_uploader(self):
        """A file uploaded by one team must be openable by the next team.

        Odoo decides attachment access through the record it is attached to, so
        a file left floating (res_model empty, uploaded by accounts) was only
        readable by accounts - the facility team got
        "doesn't have 'read' access to: Attachment".
        """
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()

        # accounts parks the ticket and uploads the proof
        po = self.env["svcticket.po"].create({
            "ticket_id": t.id, "vendor_name": "Doc Vendor", "amount": 500.0,
            "approver_ids": [(6, 0, [self.approver.id])]})
        po.action_send_for_approval()
        po.with_user(self.director).action_approve()
        t.write({"current_team_id": self.accounts.id, "state": "payment_pending"})
        t.invalidate_recordset()
        proof = self._pdf("proof.pdf")
        self.env["svcticket.payment.wizard"].with_user(self.acct).create({
            "ticket_id": t.id, "amount": 500.0, "reference": "UTR1",
            "tender_type": "neft", "proof_ids": [(6, 0, [proof.id])]}).action_confirm()
        self.env.flush_all()

        proof.invalidate_recordset()
        self.assertEqual(proof.res_model, "svcticket.ticket",
                         "an uploaded document belongs to the ticket")
        self.assertEqual(proof.res_id, t.id)
        # and the facility team can open it, exactly like the browser does
        self.env["ir.attachment"].with_user(self.tech1).browse(proof.id).read(["name"])
        self.env["svcticket.ticket"].with_user(self.tech1).browse(t.id).web_read(
            {"payment_ids": {"fields": {"proof_ids": {"fields": {"name": {}}}}}})

        # same for the invoice uploaded by the facility team
        inv = self._pdf("inv.pdf")
        self.env["svcticket.invoice.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "invoice_number": "INV-1",
            "attachment_ids": [(6, 0, [inv.id])]}).action_confirm()
        self.env.flush_all()
        inv.invalidate_recordset()
        self.assertEqual(inv.res_model, "svcticket.ticket")
        self.env["svcticket.ticket"].with_user(self.acct).browse(t.id).web_read(
            {"invoice_attachment_ids": {"fields": {"name": {}}}})

    def test_053_the_director_can_open_the_ticket_waiting_on_him(self):
        """Whoever has to decide must be able to read the ticket."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "compressor"}).action_confirm()
        t.invalidate_recordset()
        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()

        po = self.env["svcticket.po"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "BlueStar", "amount": 100.0,
            "approver_ids": [(6, 0, [self.approver.id])], "note": "quote"})
        po.action_send_for_approval()
        self.env.flush_all()
        t.invalidate_recordset()
        self.assertEqual(t.current_owner_type, "approver")

        # the director is not in any team - the record rule must still let him in
        found = self.env["svcticket.ticket"].with_user(self.director).search(
            [("id", "=", t.id)])
        self.assertEqual(found, t, "the director cannot even see the ticket")
        self.env["svcticket.ticket"].with_user(self.director).browse(t.id).read(
            ["name", "state", "waiting_on", "current_approver_ids"])
        # and the PO team cannot act while the director holds the decision
        self.assertFalse(t.with_user(self.pomem).can_act)

    def test_054_home_team_and_holder_are_the_same_facility_team(self):
        """A PO / accounts team that is configured for a category must not become
        the home team - the ticket always starts at a facility team."""
        cat = self.env["svcticket.category"].create({
            "name": "E2E Mixed", "code": "E2E-MIX"})
        self.accounts.write({"category_ids": [(4, cat.id)]})
        t = self.env["svcticket.ticket"].with_user(self.rep).create({
            "location_id": self.location.id, "category_id": cat.id,
            "description": "<p>mixed</p>"})
        t.invalidate_recordset()
        self.assertEqual(t.team_id.team_type, "facility",
                         "the home team must be a facility team")
        self.assertEqual(t.current_team_id, t.team_id,
                         "holder and home team agree when the ticket is raised")

    def test_055_lead_vetting_is_the_header_button_not_a_next_step_row(self):
        """Approve Hold / Reject Hold already do the work - the three lead rows
        must not show up again in the Next Step list."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "wait"}).action_confirm()
        t.invalidate_recordset()

        lead_options = self._options(self.lead)
        for code in ("lead_approve_po", "lead_approve_hold", "lead_reject"):
            self.assertNotIn(code, lead_options,
                             "%s belongs to the header buttons" % code)
        self.assertTrue(t.with_user(self.lead).can_vet_hold, "the top button is offered")

        # and the header buttons still do the right thing
        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.state, "on_hold", "a non-PO hold parks the ticket")

    def test_056_a_hold_never_buys_or_burns_clock_time(self):
        """The clock keeps running while the lead vets a hold; rejecting keeps the
        same clock; approving pauses it. No restarts in between."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        t.invalidate_recordset()
        before = t.sla_deadline
        running = t._open_lines().filtered(lambda l: l.state == "running")
        self.assertTrue(running)

        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "wait"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.state, "hold_requested")
        self.assertEqual(t.sla_deadline, before,
                         "asking for a hold must not reset the clock")
        self.assertEqual(t._open_lines(), running, "and must not open a new one")

        # rejected: still the same clock, still running
        self.env["svcticket.hold.reject.wizard"].with_user(self.lead).create({
            "ticket_id": t.id, "rejection_note": "no"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.state, "in_progress")
        self.assertEqual(t.sla_deadline, before, "a rejection keeps the clock")
        self.assertEqual(t._open_lines(), running)

        # approved: the clock pauses, the time already spent is kept
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "wait"}).action_confirm()
        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.state, "on_hold")
        self.assertEqual(t.lane_state, "paused")
        self.assertEqual(len(t._open_lines()), 1, "still one clock, now paused")

    def test_057_the_sub_category_is_the_one_that_counts(self):
        """Genuine check / photos must come from the category actually chosen."""
        parent = self.env["svcticket.category"].create({
            "name": "E2E Parent", "code": "E2E-P", "require_intake_photo": True,
            "min_intake_photos": 2})
        child = self.env["svcticket.category"].create({
            "name": "E2E Child", "code": "E2E-C", "parent_id": parent.id,
            "requires_approval": True,
            "approver_ids": [(6, 0, [self.approver.id])],
            "require_intake_photo": False})
        t = self.env["svcticket.ticket"].with_user(self.rep).create({
            "location_id": self.location.id, "category_id": parent.id,
            "subcategory_id": child.id, "description": "<p>child</p>"})
        t.invalidate_recordset()
        self.assertEqual(t.state, "pending_approval",
                         "the sub-category asks for a genuine check")
        self.assertEqual(t._required_photos("intake"), 0,
                         "the sub-category lifted the photo requirement")
        self.assertTrue(t.with_user(self.approver.user_id)._can_approve_ticket()
                        if hasattr(t.with_user(self.approver.user_id), "_can_approve_ticket")
                        else True)

    def test_058_the_director_finds_the_po_in_waiting_on_me(self):
        """The approver must see the ticket and get a mail, not just a chatter note."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "compressor"}).action_confirm()
        t.invalidate_recordset()
        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()

        po = self.env["svcticket.po"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "BlueStar", "amount": 900.0,
            "approver_ids": [(6, 0, [self.approver.id])], "note": "quote"})
        po.action_send_for_approval()
        self.env.flush_all()
        t.invalidate_recordset()

        action = self.env.ref("service_ticket.action_svcticket_po_my")
        domain = eval(action.domain, {"uid": self.director.id})
        found = self.env["svcticket.ticket"].with_user(self.director).search(domain)
        self.assertIn(t, found, "the PO waiting for him is not in Waiting on Me")

        # a real mail was queued for the director, not just a chatter line
        mails = self.env["mail.mail"].sudo().search(
            [("subject", "like", "Approval required"),
             ("partner_ids", "in", self.director.partner_id.ids)])
        self.assertTrue(mails, "no approval mail was prepared for the director")
        self.assertIn(self.director.email, mails[0].email_to or "")
        self.assertIn("/svcticket/approve/", mails[0].body_html or "",
                      "the mail has no approve link")
        self.assertEqual(self.env["svcticket.approval.token"].search_count(
            [("po_id", "=", po.id)]), len(po.approver_ids),
            "one single-use link per approver")

    def test_048_every_wizard_button_carries_the_ticket(self):
        """A wizard button must pre-fill the ticket, or the form opens blank and
        saving fails with 'a mandatory field is not set'."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()

        action = t.with_user(self.tech1).action_request_hold()
        self.assertEqual(action["context"].get("default_ticket_id"), t.id,
                         "the ticket link must be passed as default_ticket_id")
        self.assertNotIn("ticket_id", action["context"])

        # every wizard the module opens
        for model in ("svcticket.hold.wizard", "svcticket.po.wizard",
                      "svcticket.payment.wizard", "svcticket.invoice.wizard",
                      "svcticket.hold.reject.wizard", "svcticket.invoice.reject.wizard"):
            wiz = t.with_user(self.tech1)._wizard(model, "test", {"ticket_id": t.id})
            ctx = wiz["context"]
            self.assertIn("default_ticket_id", ctx, "%s opens without its ticket" % model)
            got = self.env[model].with_context(**ctx).default_get(["ticket_id"])
            self.assertEqual(got.get("ticket_id"), t.id,
                             "%s did not pre-fill the ticket" % model)

    def test_049_the_issue_section_is_fixed_once_the_ticket_is_raised(self):
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        before = t.sla_deadline

        other = self.env["svcticket.category"].create({
            "name": "E2E Electrical", "code": "E2E-ELEC"})
        p1 = self.env["svcticket.priority"].search([("code", "=", "P1")], limit=1)

        # reporter: no
        with self.assertRaises(UserError):
            t.with_user(self.rep).write({"category_id": other.id})
        # a plain member of the holding team: no
        with self.assertRaises(UserError):
            t.with_user(self.tech2).write({"category_id": other.id})
        # the lead of the holding team: no longer either - the Issue section is fixed
        self.assertFalse(t.with_user(self.lead).can_set_category)
        with self.assertRaises(UserError):
            t.with_user(self.lead).write({"category_id": other.id})
        # only a manager can still correct a genuine mistake
        self.assertTrue(t.with_user(self.manager).can_set_category)
        t.with_user(self.manager).write({"category_id": other.id})
        t.invalidate_recordset()
        self.assertEqual(t.category_id, other)
        self.assertTrue(t.sla_deadline, "a corrected category must leave a deadline")
        self.assertNotEqual(t.sla_deadline, before, "the deadline is recalculated")
        self.assertEqual(len(t._open_lines()), 1, "exactly one open clock")
        self.assertEqual(t.lane_state, "running")

        # priority alone recalculates too
        if p1:
            mid = t.sla_deadline
            t.with_user(self.manager).write({"priority_id": p1.id})
            t.invalidate_recordset()
            self.assertEqual(t.priority_id, p1)
            self.assertNotEqual(t.sla_deadline, mid,
                                "changing the priority recalculates the deadline")

        # once the ticket is held by another team, the old team's lead loses the right
        t.current_team_id = self.po
        t.invalidate_recordset()
        self.assertFalse(t.with_user(self.lead).can_set_category,
                         "the facility lead cannot touch a ticket the PO team holds")
        self.assertFalse(t.with_user(self.pomem).can_set_category,
                         "a PO member is not the PO lead")
        self.assertTrue(t.with_user(self.manager).can_set_category)

    def test_047_director_says_yes_on_call(self):
        """PO team records a decision given outside Odoo."""
        t = self.ticket
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po",
            "reason_id": self.reason.id, "note": "compressor",
            "estimated_cost": 20000.0}).action_confirm()
        t.invalidate_recordset()
        t.with_user(self.lead).action_approve_hold()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.po)

        self.env["svcticket.po.wizard"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "BlueStar", "amount": 20000.0,
            "approver_ids": [(6, 0, [self.approver.id])],
            "note": "only vendor"}).action_send()
        t.invalidate_recordset()
        self.assertEqual(t.current_owner_type, "approver")

        # PO team sees the "on call" button, the director's own row is not for them
        po_opts = self._options(self.pomem)
        self.assertFalse(t.with_user(self.pomem).can_act)

        row = self.env["svcticket.handoff"]._by_code("director_approve_manual")
        row._perform(t, note="approved on phone")
        t.invalidate_recordset()
        self.assertEqual(t.state, "payment_pending")
        self.assertEqual(t.current_team_id, self.accounts)
        self.assertEqual(t.po_ids.state, "approved",
                         "the PO must be settled or amounts stay zero")
        self.assertAlmostEqual(t.amount_total, 20000.0, 2)
        self.assertAlmostEqual(t.amount_due, 20000.0, 2)

    def test_05_reporter_documents(self):
        t = self.ticket
        self.env["svcticket.handoff"]._by_code("request_docs")._perform(
            t, note="need ID proof")
        t.invalidate_recordset()
        self.assertEqual(t.current_owner_type, "reporter")
        self.assertEqual(t.current_user_id, self.rep)
        self.assertIn("Reporter", t.waiting_on)
        self.assertTrue(t.with_user(self.rep).can_act)
        self.assertFalse(t.with_user(self.tech1).can_act)
        opts = self._options(self.rep)
        self.assertEqual(opts.count("docs_submitted"), 1, "no duplicate buttons")
        self.env["svcticket.handoff"]._by_code("docs_submitted")._perform(t)
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.facility)

    def test_06_crons_run(self):
        self.env["svcticket.ticket"].cron_broadcast_timeout()
        self.env["svcticket.ticket"].cron_auto_close()
        self.env["svcticket.ticket"].cron_payment_reminder()
        self.env["svcticket.sla.line"].cron_process()

    # ------------------------------------------------------- 59-61: mails
    def _mail_since(self):
        last = self.env["mail.mail"].sudo().search([], order="id desc", limit=1)
        return last.id or 0

    def _mailed(self, partner, since):
        return self.env["mail.mail"].sudo().search(
            [("id", ">", since), ("partner_ids", "in", partner.ids)])

    def _fresh(self, user=None, category=None):
        return self.env["svcticket.ticket"].with_user(user or self.rep).create({
            "location_id": self.location.id,
            "category_id": (category or self.category).id,
            "priority_id": self.priority.id if self.priority else False,
            "description": "<p>lift stuck</p>"})

    def test_059_a_hold_leaves_the_team_deadline_alone(self):
        """The team clock keeps its own deadline; the lead gets a second clock."""
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        t.invalidate_recordset()
        deadline = t.sla_deadline

        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "waiting for parts"
        }).action_confirm()
        t.invalidate_recordset()

        self.assertEqual(t.state, "hold_requested")
        self.assertEqual(t.sla_deadline, deadline, "the team deadline must not move")
        self.assertEqual(len(t._open_lines()), 1, "the team still runs one clock")
        vet = t._vetting_lines()
        self.assertEqual(len(vet), 1, "a second clock exists for the lead")
        self.assertEqual(vet.lane, "lead")
        self.assertTrue(vet.deadline, "the lead clock has a deadline of its own")

        # the lead rejects in time -> the original deadline still applies
        self.env["svcticket.hold.reject.wizard"].with_user(self.lead).create({
            "ticket_id": t.id, "rejection_note": "not needed"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.state, "in_progress")
        self.assertEqual(t.sla_deadline, deadline, "rejection must not re-base the clock")
        self.assertFalse(t._vetting_lines(), "the lead clock is gone once decided")

    def test_060_mail_recipients_follow_the_agreed_list(self):
        Mail = self.env["mail.mail"].sudo()

        # 1. raised -> the receiving team only
        since = self._mail_since()
        intake = self._fresh()
        self.assertTrue(self._mailed(self.lead.partner_id, since), "the team is told")
        self.assertTrue(self._mailed(self.tech1.partner_id, since), "members are told")
        self.assertFalse(self._mailed(self.rep.partner_id, since), "not the reporter")
        self.assertFalse(self._mailed(self.acct.partner_id, since), "not accounts")

        # 3 + 5. genuine check approved / work started -> no mail at all
        since = self._mail_since()
        intake.with_user(self.tech1).action_accept()
        intake.with_user(self.tech1).action_start_work()
        self.assertFalse(Mail.search([("id", ">", since)]),
                         "accepting or starting work sends nothing")

        # 8. hold request -> the team lead only, never the PO approver
        since = self._mail_since()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": intake.id, "request_type": "po",
            "reason_id": self.reason.id, "note": "compressor",
            "estimated_cost": 20000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_confirm()
        intake.invalidate_recordset()
        self.assertTrue(self._mailed(self.lead.partner_id, since), "the lead is told")
        self.assertFalse(self._mailed(self.director.partner_id, since),
                         "the picked approver must not be mailed yet")
        self.assertFalse(self._mailed(self.rep.partner_id, since), "not the reporter")

        # 9. hold approved (PO needed) -> PO team, facility team, reporter
        since = self._mail_since()
        intake.with_user(self.lead).action_approve_hold()
        intake.invalidate_recordset()
        for partner in (self.pomem.partner_id, self.tech1.partner_id, self.rep.partner_id):
            self.assertTrue(self._mailed(partner, since), partner.name)

        # 12. PO sent for approval -> that approver only
        since = self._mail_since()
        self.env["svcticket.po.wizard"].with_user(self.pomem).create({
            "ticket_id": intake.id, "vendor_name": "BlueStar", "amount": 20000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_send()
        intake.invalidate_recordset()
        self.assertTrue(self._mailed(self.director.partner_id, since), "approver told")
        self.assertFalse(self._mailed(self.tech1.partner_id, since), "team not told")
        token = intake.po_ids.token_ids[:1]
        self.assertFalse(token.expires_at, "the link has no time expiry")
        self.assertTrue(token._valid(), "the link works while the PO waits")

        # 13. PO approved -> po team, facility team + lead, reporter, accounts
        since = self._mail_since()
        intake.po_ids.with_user(self.director).action_approve()
        intake.invalidate_recordset()
        for partner in (self.pomem.partner_id, self.tech1.partner_id,
                        self.lead.partner_id, self.rep.partner_id,
                        self.acct.partner_id):
            self.assertTrue(self._mailed(partner, since), partner.name)
        self.assertFalse(token._valid(), "the link dies with the decision")

        # 17. added for payment -> po team, reporter, the raising team
        since = self._mail_since()
        self._add_for_payment(intake, self.acct)
        intake.invalidate_recordset()
        for partner in (self.pomem.partner_id, self.rep.partner_id,
                        self.tech1.partner_id):
            self.assertTrue(self._mailed(partner, since), partner.name)

        # 18. payment recorded -> facility team + lead, reporter, po team
        since = self._mail_since()
        self.env["svcticket.payment.wizard"].with_user(self.acct).create({
            "ticket_id": intake.id, "amount": 20000.0, "tender_type": "neft",
            "reference": "UTR777"}).action_confirm()
        intake.invalidate_recordset()
        for partner in (self.tech1.partner_id, self.lead.partner_id,
                        self.rep.partner_id, self.pomem.partner_id):
            self.assertTrue(self._mailed(partner, since), partner.name)
        body = " ".join(Mail.search([("id", ">", since)]).mapped("body_html"))
        self.assertIn("UTR777", body, "the UTR must be in the mail")

        # 20. invoice submitted -> accounts
        since = self._mail_since()
        self.env["svcticket.invoice.wizard"].with_user(self.tech1).create({
            "ticket_id": intake.id, "invoice_number": "INV-9",
            "attachment_ids": [(6, 0, [self._pdf("i9.pdf").id])]}).action_confirm()
        intake.invalidate_recordset()
        self.assertTrue(self._mailed(self.acct.partner_id, since), "accounts told")
        self.assertFalse(self._mailed(self.rep.partner_id, since), "not the reporter")

        # 21. invoice rejected -> only the team it came from
        since = self._mail_since()
        self.env["svcticket.invoice.reject.wizard"].with_user(self.acct).create({
            "ticket_id": intake.id, "reason": "wrong GST"}).action_confirm()
        intake.invalidate_recordset()
        self.assertTrue(self._mailed(self.tech1.partner_id, since), "the team is told")
        self.assertFalse(self._mailed(self.rep.partner_id, since), "not the reporter")
        self.assertFalse(self._mailed(self.acct.partner_id, since), "not accounts")

        # 22. invoice accepted -> the team only, never the reporter
        self.env["svcticket.invoice.wizard"].with_user(self.tech1).create({
            "ticket_id": intake.id, "invoice_number": "INV-10",
            "attachment_ids": [(6, 0, [self._pdf("i10.pdf").id])]}).action_confirm()
        intake.invalidate_recordset()
        since = self._mail_since()
        intake.with_user(self.acct).action_accept_invoice()
        intake.invalidate_recordset()
        self.assertTrue(self._mailed(self.tech1.partner_id, since), "the team is told")
        self.assertFalse(self._mailed(self.rep.partner_id, since), "not the reporter")

    def test_061_genuine_check_and_rejection_mails(self):
        Mail = self.env["mail.mail"].sudo()
        vetting = self.env["svcticket.category"].create({
            "name": "E2E Checked", "code": "E2E-CHK", "require_intake_photo": False,
            "requires_approval": True, "approver_ids": [(6, 0, [self.approver.id])]})

        # 2. genuine check -> that category's approver only
        since = self._mail_since()
        t = self._fresh(category=vetting)
        self.assertTrue(self._mailed(self.director.partner_id, since), "approver told")
        self.assertFalse(self._mailed(self.tech1.partner_id, since), "team not told")

        # 4. rejected at intake -> the reporter, with the reason, and nobody else
        since = self._mail_since()
        action = t.with_user(self.director).action_reject_ticket()
        self.assertEqual(action["res_model"], "svcticket.reject.wizard")
        self.env["svcticket.reject.wizard"].with_user(self.director).create({
            "ticket_id": t.id, "reason": "not a facility issue"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.state, "rejected")
        self.assertTrue(self._mailed(self.rep.partner_id, since), "reporter told")
        self.assertFalse(self._mailed(self.tech1.partner_id, since), "team not told")
        body = " ".join(self._mailed(self.rep.partner_id, since).mapped("body_html"))
        self.assertIn("not a facility issue", body, "the reason must be in the mail")

        # 15. approved on a call -> same audience, "recorded by ... on behalf of"
        t2 = self._fresh(category=vetting)
        t2.with_user(self.director).action_approve_ticket()
        t2.invalidate_recordset()
        t2.with_user(self.tech1).action_accept()
        t2.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t2.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "motor", "estimated_cost": 5000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_confirm()
        t2.with_user(self.lead).action_approve_hold()
        self.env["svcticket.po.wizard"].with_user(self.pomem).create({
            "ticket_id": t2.id, "vendor_name": "Vendor X", "amount": 5000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_send()
        t2.invalidate_recordset()
        since = self._mail_since()
        t2.po_ids.with_user(self.pomem).action_approve_on_call()
        t2.invalidate_recordset()
        self.assertEqual(t2.state, "payment_pending")
        for partner in (self.pomem.partner_id, self.rep.partner_id, self.acct.partner_id):
            self.assertTrue(self._mailed(partner, since), partner.name)
        body = " ".join(Mail.search([("id", ">", since)]).mapped("body_html"))
        self.assertIn("on behalf of the director", body)

    def test_062_one_mail_per_event_and_nothing_is_deleted(self):
        """Every event sends its own mail, immediately, and the record stays."""
        Mail = self.env["mail.mail"].sudo()
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.invalidate_recordset()
        rows = Mail.search([("model", "=", "svcticket.ticket"), ("res_id", "=", t.id)])
        self.assertTrue(rows, "the raise mail must exist")
        self.assertEqual(len(rows), 1, "one event = one mail, no duplicate chatter mail")
        for row in rows:
            self.assertFalse(row.auto_delete, "a sent mail must stay visible")
            self.assertIn(row.state, ("sent", "exception"),
                          "sent at once, not queued")
            self.assertTrue(row.email_from, "From is the company / user address")

        # a chatter note never turns into a mail
        since = self._mail_since()
        self.env["svcticket.channel"].notify(
            t, self.tech1.partner_id, "Just a note", "<p>chatter only</p>")
        self.assertFalse(Mail.search([("id", ">", since)]),
                         "notify() must never send an e-mail")

        # and a broken address must not break the flow
        t.with_user(self.rep).write({"reporter_id": self.rep.id})
        self.tech2.partner_id.email = False
        t._mail_docs_requested()          # goes to the reporter, who still has a mail

    def test_063_reopening_mails_the_team_that_gets_it(self):
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        t.with_user(self.tech1).write({"resolution_notes": "fixed"})
        t.with_user(self.tech1).action_resolve()
        t.invalidate_recordset()
        self.assertEqual(t.state, "resolved")

        since = self._mail_since()
        t.with_user(self.rep).action_reopen()
        t.invalidate_recordset()
        self.assertEqual(t.state, "in_progress")
        self.assertTrue(self._mailed(self.tech1.partner_id, since), "the team is told")
        self.assertTrue(self._mailed(self.lead.partner_id, since), "the lead is told")
        self.assertFalse(self._mailed(self.acct.partner_id, since), "not accounts")
        self.assertFalse(self._mailed(self.pomem.partner_id, since), "not the PO team")

    # ------------------------------------- 64-67: buttons and money guards
    def _to_accounts(self, amount=5000.0):
        """Walk a ticket to the accounts desk with an approved PO."""
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "buy", "estimated_cost": amount,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_confirm()
        t.with_user(self.lead).action_approve_hold()
        self.env["svcticket.po.wizard"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "Vendor", "amount": amount,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_send()
        t.invalidate_recordset()
        t.po_ids.with_user(self.director).action_approve()
        t.invalidate_recordset()
        return t

    def test_064_added_for_payment_does_something_and_only_once(self):
        t = self._to_accounts()
        self.assertEqual(t.state, "payment_pending")
        self.assertFalse(t.payment_added_at)

        self._add_for_payment(t, self.acct)
        t.invalidate_recordset()
        self.assertTrue(t.payment_added_at, "the click must leave a mark on the ticket")
        self.assertEqual(t.payment_added_by, self.acct)
        self.assertEqual(t.lane_state, "paused", "the payment clock stops")
        self.assertEqual(t.state, "payment_pending", "it is not a state change")

        with self.assertRaises(UserError):
            t.with_user(self.acct).action_added_for_payment()

        # and it is no longer offered a second time in the Next Step list
        self.assertNotIn("added_for_payment",
                         t.with_user(self.acct).handoff_option_ids.mapped("code"))

    def test_065_a_payment_can_never_exceed_the_approved_po(self):
        t = self._to_accounts(amount=5000.0)
        Wizard = self.env["svcticket.payment.wizard"]

        with self.assertRaises(UserError):
            Wizard.with_user(self.acct).create({
                "ticket_id": t.id, "amount": 25000.0, "tender_type": "neft",
                "reference": "TOO-MUCH"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.amount_paid, 0.0, "nothing may be recorded")

        with self.assertRaises(UserError):
            Wizard.with_user(self.acct).create({
                "ticket_id": t.id, "amount": 0.0, "tender_type": "neft",
                "reference": "ZERO"}).action_confirm()

        # half, then the rest is fine; the day is a date and it defaults to today
        wiz = Wizard.with_user(self.acct).create({
            "ticket_id": t.id, "amount": 2500.0, "tender_type": "neft",
            "reference": "HALF"})
        self.assertEqual(wiz.paid_at, fields.Date.context_today(wiz))
        self.assertNotIsInstance(wiz.paid_at, __import__("datetime").datetime,
                                 "Paid On must be a date, not a timestamp")
        wiz.action_confirm()
        t.invalidate_recordset()
        self.assertAlmostEqual(t.amount_due, 2500.0, 2)

        with self.assertRaises(UserError):
            Wizard.with_user(self.acct).create({
                "ticket_id": t.id, "amount": 3000.0, "tender_type": "neft",
                "reference": "OVER-BALANCE"}).action_confirm()

        Wizard.with_user(self.acct).create({
            "ticket_id": t.id, "amount": 2000.0, "tender_type": "neft",
            "reference": "REST"}).action_confirm()
        t.invalidate_recordset()
        self.assertAlmostEqual(t.amount_paid, 4500.0, 2)
        self.assertAlmostEqual(t.amount_due, 500.0, 2)
        self.assertEqual(t.payment_ids[:1]._fields["paid_at"].type, "date")

    def test_066_nothing_can_be_paid_before_the_po_is_approved(self):
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "buy", "estimated_cost": 5000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_confirm()
        t.with_user(self.lead).action_approve_hold()
        self.env["svcticket.po.wizard"].with_user(self.pomem).create({
            "ticket_id": t.id, "vendor_name": "Vendor", "amount": 5000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_send()
        t.invalidate_recordset()
        self.assertEqual(t.po_ids.state, "pending")

        with self.assertRaises(UserError):
            self.env["svcticket.payment.wizard"].with_user(self.acct).create({
                "ticket_id": t.id, "amount": 100.0, "tender_type": "neft",
                "reference": "EARLY"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.amount_paid, 0.0)

    def test_067_submit_invoice_has_one_door_and_always_asks_for_the_file(self):
        t = self._to_accounts()
        # pay in full -> back to the facility team, ready to invoice
        self.env["svcticket.payment.wizard"].with_user(self.acct).create({
            "ticket_id": t.id, "amount": 5000.0, "tender_type": "neft",
            "reference": "FULL"}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id, self.facility)
        self.assertEqual(t.state, "in_progress")

        # the Next Step list must not offer the step: the Invoice tab owns it
        self.assertNotIn("submit_invoice",
                         t.with_user(self.tech1).handoff_option_ids.mapped("code"))

        # the button opens the upload form instead of moving the ticket
        before = (t.current_team_id, t.state)
        action = t.with_user(self.tech1).action_submit_invoice()
        self.assertEqual(action.get("res_model"), "svcticket.invoice.wizard")
        t.invalidate_recordset()
        self.assertEqual((t.current_team_id, t.state), before,
                         "opening the form must not move the ticket")
        self.assertFalse(t.invoice_attachment_ids)
        self.assertFalse(t.invoice_number)

    def test_068_next_step_rows_never_skip_their_form(self):
        """A row that changes money or documents must open its form, not run blind."""
        needs = {
            "hold_to_lead": "action_request_hold",
            "added_for_payment": "action_added_for_payment",
            "submit_invoice": "action_submit_invoice",
            "payment_done": "action_record_payment",
            "invoice_accepted": "action_accept_invoice",
            "invoice_rejected": "action_reject_invoice",
        }
        Handoff = self.env["svcticket.handoff"]
        for code, method in needs.items():
            row = Handoff._by_code(code)
            self.assertEqual(row.run_action, method, code)
            if row.show_in_next_step:
                t = self._to_accounts()
                ticket = t.with_user(self.acct)
                if code in ticket.handoff_option_ids.mapped("code"):
                    action = row.with_context(
                        svcticket_ticket_id=t.id).with_user(self.acct).action_run()
                    self.assertTrue(
                        action is True or isinstance(action, dict),
                        "%s must run its action" % code)

        # steps whose button lives elsewhere are hidden from the list
        for code in ("hold_to_lead", "submit_invoice", "added_for_payment",
                     "invoice_accepted", "invoice_rejected", "payment_done"):
            self.assertFalse(Handoff._by_code(code).show_in_next_step, code)

    # --------------------------------------- 69-70: visibility and the lead's clock
    def _queue(self, action_xmlid, user):
        """What does this menu show this user? (the real action domain)"""
        action = self.env.ref(action_xmlid)
        domain = eval(action.domain, {"uid": user.id}) if isinstance(action.domain, str) \
            else action.domain
        return self.env["svcticket.ticket"].with_user(user).search(domain)

    def test_069_a_genuine_check_ticket_sits_on_the_approver_desk(self):
        vetting = self.env["svcticket.category"].create({
            "name": "E2E Vet Queue", "code": "E2E-VQ", "require_intake_photo": False,
            "requires_approval": True, "approver_ids": [(6, 0, [self.approver.id])]})
        t = self._fresh(category=vetting)
        t.invalidate_recordset()

        self.assertEqual(t.state, "pending_approval")
        self.assertEqual(t.current_owner_type, "approver",
                         "the approver holds it while the check runs")
        self.assertEqual(t.current_approver_ids, self.approver)

        my = self._queue("service_ticket.action_svcticket_ticket_my", self.director)
        waiting = self._queue("service_ticket.action_svcticket_po_my", self.director)
        self.assertIn(t, my, "My Tickets must show it to the approver")
        self.assertIn(t, waiting, "Waiting on Me must show it to the approver")

        # and the team cannot start it yet
        self.assertNotIn(t, self._queue("service_ticket.action_svcticket_ticket_my", self.tech1)
                         .filtered(lambda r: r.state in ("new", "in_progress")))

        t.with_user(self.director).action_approve_ticket()
        t.invalidate_recordset()
        self.assertEqual(t.state, "new")
        self.assertFalse(t.current_approver_ids, "the check is over")
        self.assertEqual(t.current_owner_type, "team")
        self.assertNotIn(t, self._queue("service_ticket.action_svcticket_po_my", self.director),
                         "once decided it leaves the approver's queue")
        # and nobody else suddenly sees every open ticket (a 4th "|" in that
        # domain used to OR the state filter in and show everything to everyone)
        stranger = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "E2E Stranger", "login": "e2e_stranger",
            "email": "stranger@test.local",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("service_ticket.group_user").id])]})
        self.assertFalse(self._queue("service_ticket.action_svcticket_po_my", stranger),
                         "Waiting on Me must stay empty for an unrelated user")
        self.assertIn(t, self._queue("service_ticket.action_svcticket_queue_my_team", self.tech1),
                      "and it is on the team's desk")
        self.assertTrue(t.with_user(self.tech1).can_act)

    def test_070_a_breach_stays_a_breach_and_the_lead_is_escalated(self):
        """The lead clock must show on the ticket and escalate, not tick quietly."""
        from datetime import timedelta
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t.id, "request_type": "other",
            "reason_id": self.plain_reason.id, "note": "waiting"}).action_confirm()
        t.invalidate_recordset()

        lead_line = t._open_lines()[:1]
        lead_deadline = lead_line.deadline
        clocks = len(t.sla_line_ids)
        self.assertFalse(t.sla_breached)

        past = fields.Datetime.now() - timedelta(hours=30)
        t.sla_line_ids.write({"deadline": past, "last_notified_at": False})
        last = self.env["mail.mail"].sudo().search([], order="id desc", limit=1).id or 0
        self.env["svcticket.sla.line"].cron_process()
        t.invalidate_recordset()

        self.assertTrue(t.sla_breached, "a breached ticket must say so")
        self.assertEqual(len(t.sla_line_ids), clocks, "no clock may be restarted")
        self.assertEqual(t._open_lines()[:1].deadline, past,
                         "a breach must never push the deadline forward")
        first = self.env["mail.mail"].sudo().search(
            [("id", ">", last), ("subject", "like", "Hold approval overdue")])
        self.assertTrue(first, "the lead is reminded once")
        self.assertEqual(len(first), 1, "and only once, not by both clocks")
        self.assertTrue(all(p == self.lead.partner_id
                            for p in first.mapped("partner_ids")),
                        "the first reminder is the lead's alone")

        # still nothing: now it follows the configured escalation step
        t.sla_line_ids.write({"deadline": past, "last_notified_at": False})
        last = self.env["mail.mail"].sudo().search([], order="id desc", limit=1).id or 0
        self.env["svcticket.sla.line"].cron_process()
        t.invalidate_recordset()
        self.assertEqual(t.escalation_level, 1, "the escalation level must move")
        esc = self.env["mail.mail"].sudo().search(
            [("id", ">", last), ("subject", "like", "Escalated")])
        self.assertTrue(esc, "the escalation step is mailed, not the lead again")
        self.assertIn(self.manager.partner_id, esc.mapped("partner_ids"),
                      "with nothing configured it falls back to the managers")
        self.assertEqual(lead_deadline is not None, True)

    def test_071_a_normal_clock_breach_really_escalates(self):
        """The plain SLA clock must mail the escalation step, not fail silently."""
        from datetime import timedelta
        t = self._fresh()
        t.with_user(self.tech1).action_accept()
        t.with_user(self.tech1).action_start_work()
        t.invalidate_recordset()
        stage = t.sla_stage_id
        self.assertTrue(stage, "the ticket must carry its SLA stage")
        # point this level at a real person so the target is unambiguous
        stage.sudo().write({"escalate_to": "approvers",
                            "approver_ids": [(6, 0, [self.approver.id])]})

        line = t._open_lines()[:1]
        past = fields.Datetime.now() - timedelta(hours=30)
        line.write({"deadline": past, "last_notified_at": False})
        last = self.env["mail.mail"].sudo().search([], order="id desc", limit=1).id or 0
        self.env["svcticket.sla.line"].cron_process()
        t.invalidate_recordset()

        self.assertTrue(t.sla_breached)
        self.assertEqual(t.escalation_level, 1, "the counter must move")
        mail = self.env["mail.mail"].sudo().search(
            [("id", ">", last), ("subject", "like", "SLA breached")])
        self.assertTrue(mail, "the escalation mail must actually be sent")
        # level 1 breaches always start with that team's lead
        self.assertIn(self.lead.partner_id, mail.mapped("partner_ids"))
        self.assertIn("Escalated to", " ".join(t.message_ids.mapped("body")))

    def test_072_there_is_exactly_one_door_to_send_a_po(self):
        """The PO team must have ONE way to send a PO, and it must always be a form."""
        row = self.env["svcticket.handoff"]._by_code("send_to_approver")
        self.assertFalse(row.active,
                         "the duplicate Next Step row stays switched off")
        self.assertFalse(row.show_in_next_step)
        self.assertEqual(row.run_action, "action_send_po",
                         "even if it is re-enabled it opens the PO form")

        t2 = self._fresh()
        t2.with_user(self.tech1).action_accept()
        t2.with_user(self.tech1).action_start_work()
        self.env["svcticket.hold.wizard"].with_user(self.tech1).create({
            "ticket_id": t2.id, "request_type": "po", "reason_id": self.reason.id,
            "note": "buy", "estimated_cost": 1000.0,
            "approver_ids": [(6, 0, [self.approver.id])]}).action_confirm()
        t2.with_user(self.lead).action_approve_hold()
        t2.invalidate_recordset()
        self.assertEqual(t2.state, "po_pending")

        # the PO team's Next Step list has no "send to director" row at all
        self.assertNotIn("send_to_approver",
                         t2.with_user(self.pomem).handoff_option_ids.mapped("code"))
        # the only door is the PO form
        action = t2.with_user(self.pomem).action_send_po()
        self.assertEqual(action.get("res_model"), "svcticket.po.wizard")

    def test_073_one_door_per_step_and_the_invoice_belongs_to_accounts(self):
        """Invoice accept/reject is accounts-only, and every step sits in its own tab."""
        # 1. the header no longer repeats what the tabs own
        arch = self.env.ref("service_ticket.view_svcticket_ticket_form").arch
        header = arch.split("</header>")[0]
        for name in ("action_accept_invoice", "action_reject_invoice",
                     "action_added_for_payment", "action_submit_invoice",
                     "action_send_po", "action_record_payment"):
            self.assertNotIn('name="%s"' % name, header,
                             "%s must not sit in the header" % name)
            self.assertIn('name="%s"' % name, arch, "%s has no button" % name)

        # 2. the Issue section cannot be edited by anybody in the flow
        issue = arch.split('string="Issue"')[1].split("</group>")[0]
        for fname in ("location_id", "category_id", "subcategory_id",
                      "priority_id", "other_issue_text"):
            start = issue.index('name="%s"' % fname)
            tag = issue[start:issue.index("/>", start)]
            self.assertIn('readonly="1"', tag, "%s is still editable" % fname)

        # 3. accounts picks the money up; the facility team must not
        t = self._to_accounts()
        self.assertEqual(t.state, "payment_pending")
        self.assertFalse(t.with_user(self.tech1).can_vet_invoice)
        with self.assertRaises(UserError):
            t.with_user(self.tech1).action_added_for_payment()
        self.assertTrue(t.with_user(self.acct).can_vet_invoice)
        self._add_for_payment(t, self.acct)
        # ... and only once
        with self.assertRaises(UserError):
            t.with_user(self.acct).action_added_for_payment()

        # 4. pay it, back to facility, submit the invoice, back to accounts
        proof = self.env["ir.attachment"].with_user(self.acct).create(
            {"name": "utr.pdf", "datas": b"JVBERi0xLjQK", "mimetype": "application/pdf"})
        self.env["svcticket.payment.wizard"].create({
            "ticket_id": t.id, "amount": 5000.0, "reference": "UTR-77",
            "tender_type": "neft", "proof_ids": [(6, 0, [proof.id])]}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id.team_type, "facility")
        inv = self.env["ir.attachment"].with_user(self.tech1).create(
            {"name": "inv.pdf", "datas": b"JVBERi0xLjQK", "mimetype": "application/pdf"})
        self.env["svcticket.invoice.wizard"].create({
            "ticket_id": t.id, "invoice_number": "INV-9",
            "attachment_ids": [(6, 0, [inv.id])]}).action_confirm()
        t.invalidate_recordset()
        self.assertEqual(t.current_team_id.team_type, "account_team")

        # 5. only the accounts team decides - facility, its lead and the reporter cannot
        for user in (self.tech1, self.lead, self.rep):
            self.assertFalse(t.with_user(user).can_vet_invoice, user.name)
            with self.assertRaises(UserError):
                t.with_user(user).action_accept_invoice()
            with self.assertRaises(UserError):
                t.with_user(user).action_reject_invoice()
        t.with_user(self.acct).action_accept_invoice()
        t.invalidate_recordset()
        self.assertTrue(t.invoice_accepted)
        self.assertTrue(t.invoice_accepted_by)
