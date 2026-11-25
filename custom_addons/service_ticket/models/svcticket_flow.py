# -*- coding: utf-8 -*-
"""The state machine: every button a user can press lives here."""
import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

RESOLVED_AUTO_CLOSE_DAYS = 7  # overridable via settings


class SvcTicketFlow(models.Model):
    _inherit = "svcticket.ticket"

    # ============================================================ facility
    def _broadcast(self):
        """Tell the team a new ticket needs an owner."""
        for ticket in self:
            team = ticket.team_id
            if not team or team.assignment_mode != "broadcast" or not team.member_ids:
                continue
            self.env["svcticket.channel"].notify(
                ticket, team.member_ids.mapped("partner_id"),
                _("New ticket: %s") % ticket.name,
                _("<b>%s</b> at %s needs an owner. First to accept gets it.")
                % (ticket.name, ticket.location_id.name))

    @api.model
    def cron_broadcast_timeout(self):
        """Nobody accepted a broadcast ticket in time -> pin it on the team leads.

        Runs every 30 minutes. Only touches tickets that are still unclaimed on a
        broadcast team and are older than that team's broadcast timeout.
        """
        now = fields.Datetime.now()
        tickets = self.search([
            ("state", "in", ("new", "acknowledged")),
            ("assignee_ids", "=", False),
            ("team_id", "!=", False),
        ])
        for ticket in tickets:
            team = ticket.team_id
            if team.assignment_mode != "broadcast" or not team.lead_ids:
                continue
            timeout = team.broadcast_timeout or 4.0
            if (now - ticket.create_date).total_seconds() < timeout * 3600:
                continue
            ticket.write({"assignee_ids": [(6, 0, team.lead_ids.ids)]})
            ticket.message_post(body=_(
                "Nobody picked this up within %s hours, so it was assigned to the "
                "team lead automatically.") % timeout)
            ticket._mail_lead(
                _("Unclaimed ticket: %s") % ticket.name,
                _("<b>%s</b> at %s sat unclaimed for %s hours. It is assigned to you now.")
                % (ticket.name, ticket.location_id.name, timeout),
                [(_("Ticket"), ticket.name),
                 (_("Location"), ticket.location_id.name),
                 (_("Unclaimed for"), "%s hours" % timeout)])
            ticket._log_event("assigned", note=_("Broadcast timeout - assigned to team lead"))
        return True

    # ========================================================= communication
    def action_send_update(self):
        """Post an update: logged in the Communications tab AND mailed to
        the reporter + assignees through chatter."""
        self.ensure_one()
        body = (self.draft_message or "").strip()
        if not body or body == "<p></p>":
            raise UserError(_("You cannot send an empty update."))

        self.env["svcticket.conversation.log"].create({
            "ticket_id": self.id, "author_id": self.env.user.id,
            "message": body, "is_internal": False,
        })
        recipients = self.reporter_id.partner_id | self.assignee_ids.mapped("partner_id")
        self.message_post(
            body=body, subject=_("Update on %s - %s") % (self.name, self.location_id.name),
            partner_ids=(recipients - self.env.user.partner_id).ids,
            subtype_xmlid="mail.mt_comment", message_type="comment")
        self.draft_message = False
        return True

    def action_save_internal_note(self):
        """Internal note: logged in the Communications tab, never mailed."""
        self.ensure_one()
        body = (self.draft_message or "").strip()
        if not body or body == "<p></p>":
            raise UserError(_("You cannot save an empty internal note."))

        self.env["svcticket.conversation.log"].create({
            "ticket_id": self.id, "author_id": self.env.user.id,
            "message": body, "is_internal": True,
        })
        self.message_post(body=body, subtype_xmlid="mail.mt_note")
        self.draft_message = False
        return True

    # ======================================================= genuine check
    def _approval_approvers(self):
        """Sub-category approvers first, then the main category's."""
        self.ensure_one()
        cat = self._config_category()
        return cat.approver_ids or self.category_id.approver_ids

    def _can_approve_ticket(self):
        self.ensure_one()
        if self.env.user.has_group("service_ticket.group_manager"):
            return True
        return self.env.user in self._approval_approvers().mapped("user_id")

    def action_approve_ticket(self):
        for ticket in self:
            if ticket.state != "pending_approval":
                raise UserError(_("This ticket is not waiting for approval."))
            if not ticket._can_approve_ticket():
                raise UserError(_("Only this category's approvers can approve it."))
            ticket = ticket.sudo()   # the approver need not be in the ticket's team
            # the genuine check is over: the ticket goes back to its team
            ticket.write({"current_owner_type": "team",
                          "current_approver_ids": [(5, 0, 0)]})
            ticket._set_state("new", note=_("Approved as genuine"))
            ticket.message_post(body=_("Ticket approved by %s. Work can start.")
                                % self.env.user.name)
        return True

    def action_reject_ticket(self):
        self.ensure_one()
        if self.state != "pending_approval":
            raise UserError(_("This ticket is not waiting for approval."))
        if not self._can_approve_ticket():
            raise UserError(_("Only this category's approvers can reject it."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Reject Ticket"),
            "res_model": "svcticket.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id},
        }

    def action_do_reject_ticket(self, reason):
        """Called by the rejection wizard - the reason is kept for the reporter."""
        for ticket in self:
            if ticket.state != "pending_approval":
                raise UserError(_("This ticket is not waiting for approval."))
            if not ticket._can_approve_ticket():
                raise UserError(_("Only this category's approvers can reject it."))
            ticket = ticket.sudo()
            ticket.write({"current_approver_ids": [(5, 0, 0)]})
            ticket._open_lines().action_close("rejected")
            ticket._set_state("rejected", note=_("Rejected at intake"))
            ticket.message_post(body=_("Ticket rejected by %s.<br/>Reason: %s")
                                % (self.env.user.name, reason))
            ticket._log_event("rejected", note=reason)
            ticket._mail_intake_rejected(reason)
        return True

    def action_send_po(self):
        """PO team: fill the PO and mail it to the approvers with the PDF attached."""
        self.ensure_one()
        return self._wizard("svcticket.po.wizard", _("Send PO for Approval"),
                            {"ticket_id": self.id})

    def action_submit_intake(self):
        """Save (already done by the client) and jump to My Tickets."""
        self.ensure_one()
        return self.env["ir.actions.actions"]._for_xml_id(
            "service_ticket.action_svcticket_ticket_my")

    def action_accept(self):
        for ticket in self:
            if ticket.state != "new":
                raise UserError(_("Only new tickets can be accepted."))
            ticket.write({"assignee_ids": [(4, self.env.uid)],
                          "acknowledged_at": fields.Datetime.now()})
            ticket._set_state("acknowledged", note=_("Accepted"))
        return True

    def action_start_work(self):
        for ticket in self:
            if ticket.state not in ("new", "acknowledged"):
                raise UserError(_("Cannot start work on this ticket."))
            vals = {"started_at": ticket.started_at or fields.Datetime.now(),
                    "acknowledged_at": ticket.acknowledged_at or fields.Datetime.now()}
            if self.env.user not in ticket.assignee_ids:
                vals["assignee_ids"] = [(4, self.env.uid)]
            ticket.write(vals)
            ticket._set_state("in_progress", note=_("Work started"))
        return True

    # ================================================================ holds
    def action_request_hold(self):
        self.ensure_one()
        if self.state not in ("acknowledged", "in_progress"):
            raise UserError(_("Hold can only be requested while working on the ticket."))
        return self._wizard("svcticket.hold.wizard", _("Request Hold"),
                            {"ticket_id": self.id, "pre_hold_state": self.state})

    def _can_vet_hold(self):
        self.ensure_one()
        if self.env.user.has_group("service_ticket.group_manager"):
            return True
        team = self.current_team_id or self.team_id
        if team and self.env.user in team.lead_ids:
            return True
        return bool(self.current_owner_type == "lead"
                    and self.current_user_id == self.env.user)

    def action_approve_hold(self):
        self.ensure_one()
        if not self._can_vet_hold():
            raise UserError(_("Only the team lead can approve a hold."))
        if self.state != "hold_requested":
            raise UserError(_("This ticket is not awaiting hold approval."))

        needs_po = self.hold_needs_po or self.hold_reason_id.needs_financials
        self._close_vetting_clock("hold approved")
        self.write({"hold_started_at": fields.Datetime.now()})
        self._log_event("hold_approved", note=self.hold_reason_id.name)
        self.message_post(body=_("Hold approved by %s.") % self.env.user.name)

        row = self.env["svcticket.handoff"]._by_code(
            "lead_approve_po" if needs_po else "lead_approve_hold")
        row._perform(self, note=self.hold_reason_id.name or "")
        self._mail_hold_approved(needs_po)
        return True

    def action_reject_hold(self):
        self.ensure_one()
        if not self._can_vet_hold():
            raise UserError(_("Only the team lead can reject a hold."))
        if self.state != "hold_requested":
            raise UserError(_("This ticket is not awaiting hold approval."))
        return self._wizard("svcticket.hold.reject.wizard", _("Reject Hold"), {"ticket_id": self.id})

    def action_resume_work(self):
        self.ensure_one()
        if self.state != "on_hold":
            raise UserError(_("Only tickets on hold can be resumed."))
        if self.hold_started_at:
            self.hold_hours += (fields.Datetime.now() - self.hold_started_at).total_seconds() / 3600.0
        self.write({"hold_started_at": False})
        self._resume_clock("resumed")
        self._set_state(self.pre_hold_state or "in_progress", note=_("Resumed"))
        return True

    # =========================================================== PO / money
    def action_added_for_payment(self):
        """Accounts team marks the ticket as "added for payment".

        This is NOT a payment record - no amount, reference or date is asked
        here. The team only attaches a proof (e.g. screenshot of the email /
        payment intimation) that the payout has been initiated. The ticket
        stays with the accounts team and the payment clock pauses until the
        actual amount is recorded through "Record Payment".
        """
        self.ensure_one()
        if self.state != "payment_pending":
            raise UserError(_("This ticket is not with the accounts team."))
        if not (self.can_vet_invoice
                or self.env.user.has_group("service_ticket.group_manager")):
            raise UserError(_(
                "Only the accounts team holding %s can mark it added for payment.")
                % self.name)
        if self.payment_added_at:
            raise UserError(_(
                "%s was already added for payment by %s on %s. Record the payment "
                "instead.") % (self.name, self.payment_added_by.name or _("someone"),
                               fields.Datetime.to_string(self.payment_added_at)))
        return {
            "name": _("Added for Payment"),
            "type": "ir.actions.act_window",
            "res_model": "svcticket.added.for.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id},
        }

    def action_record_payment(self):
        self.ensure_one()
        return self._wizard("svcticket.payment.wizard", _("Record Payment"), {"ticket_id": self.id})

    def action_submit_invoice(self):
        self.ensure_one()
        if self.state not in ("in_progress",):
            raise UserError(_("Submit a tax invoice from an in-progress ticket."))
        if not self.amount_total:
            raise UserError(_("There is no approved PO to invoice against."))
        return self._wizard("svcticket.invoice.wizard", _("Submit Tax Invoice"),
                            {"ticket_id": self.id})

    # ============================================================ closing
    def action_accept_invoice(self):
        for ticket in self:
            if not (ticket.can_vet_invoice
                    or self.env.user.has_group("service_ticket.group_manager")):
                raise UserError(_(
                    "Only the accounts team holding %s can accept its tax invoice.")
                    % ticket.name)
            if not ticket.invoice_attachment_ids:
                raise UserError(_("There is no invoice attached to accept."))
            ticket.write({
                "invoice_accepted": True,
                "invoice_accepted_at": fields.Datetime.now(),
                "invoice_accepted_by": self.env.user.id,
                "invoice_reject_reason": False,
            })
            ticket.message_post(body=_("Tax invoice %s accepted by %s.") % (
                ticket.invoice_number or "", self.env.user.name))
            ticket._log_event("invoice", note=_("accepted"))
            ticket._mail_invoice_accepted(ticket.invoice_number)
            # the ticket stays with the accounts team only while there is
            # still something to pay. if the balance is already zero (paid in
            # full or waived off) there is nothing left for accounts to do -
            # return it to the facility team so it can be resolved / closed.
            if ticket.amount_due <= 0:
                facility = self.env["svcticket.team"].search(
                    [("team_type", "=", "facility"), ("active", "=", True)],
                    limit=1)
                if not facility:
                    raise UserError(_(
                        "No active facility team is configured. "
                        "Configuration > Teams."))
                ticket.write({"current_team_id": facility.id})
                ticket._open_clock("facility",
                                   note=_("Invoice accepted - fully paid"))
                ticket._set_state("in_progress",
                                  note=_("Invoice accepted - fully paid"))
            # else: remains with the accounts team (payment_pending) so they
            # can run Added for Payment -> Record Payment until the balance is 0.
        return True

    def action_reject_invoice(self):
        for ticket in self:
            if not (ticket.can_vet_invoice
                    or self.env.user.has_group("service_ticket.group_manager")):
                raise UserError(_(
                    "Only the accounts team holding %s can reject its tax invoice.")
                    % ticket.name)
            if not ticket.invoice_attachment_ids:
                raise UserError(_("There is no invoice attached to reject."))
            ticket._log_event("invoice", note=_("rejected"))
        return {
            "type": "ir.actions.act_window",
            "name": _("Reject Invoice"),
            "res_model": "svcticket.invoice.reject.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self[:1].id},
        }

    def action_mail_docs_requested(self):
        """Hook used by the routing row for "Request Documents"."""
        return self._mail_docs_requested()

    def action_mail_docs_submitted(self):
        return self._mail_docs_submitted()

    def action_resolve(self):
        for ticket in self:
            if ticket.state not in ("acknowledged", "in_progress", "payment_pending"):
                raise UserError(_("Only active tickets can be resolved."))
            if not (ticket.resolution_notes or "").strip():
                raise UserError(_("Resolution notes are required."))
            if ticket.amount_total and not ticket.invoice_accepted:
                raise UserError(_(
                    "The tax invoice has not been accepted by the accounts team yet. "
                    "No invoice, no resolution - that is the point."))
            params = ticket.env["ir.config_parameter"].sudo()
            if params.get_param("svcticket.require_resolution_photo", "1") in ("1", "True", "true"):
                ticket._check_photos("resolution")
            if ticket.amount_total and not ticket.invoice_accepted:
                raise UserError(_(
                    "The tax invoice has not been accepted by the accounts team yet. "
                    "No invoice, no resolution - that is the point."))
            ticket.write({"resolved_at": fields.Datetime.now()})
            ticket._open_lines().action_close("resolved")
            ticket._log_event("resolved")
            ticket._set_state("resolved", note=_("Resolved"))
            ticket._mail_resolved()
        return True

    def action_waive_balance(self):
        """Write off the remaining balance so the ticket can be closed / audited.

        PO 10,000 - paid 9,000 - waived 1,000 = amount due 0.
        Only the team holding the ticket (or a manager) may waive, and never more
        than what is still due.
        """
        self.ensure_one()
        if self.amount_due <= 0:
            raise UserError(_("There is no balance to waive on %s.") % self.name)
        if not (self.can_act or self.env.user.has_group("service_ticket.group_manager")):
            raise UserError(_("Only the team holding %s can waive its balance.") % self.name)
        return {
            "name": _("Waive Remaining Balance"),
            "type": "ir.actions.act_window",
            "res_model": "svcticket.balance.waive.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ticket_id": self.id,
                        "default_waive_amount": self.amount_due},
        }

    def action_mark_duplicate(self):
        """Mark the ticket as a duplicate and cancel it - only the category's
        approver may do this."""
        self.ensure_one()
        if not self.can_mark_duplicate:
            raise UserError(_(
                "Only the approver configured on the category '%s' can mark this "
                "ticket as a duplicate.") % (self.category_id.name or _("<none>")))
        if self.is_duplicate:
            raise UserError(_("%s is already marked as a duplicate.") % self.name)
        self.write({"is_duplicate": True})
        # cancel the ticket like a rejection - clock stops, out of Open
        self._open_lines().action_close("cancelled")
        self._set_state("cancelled", note=_("Cancelled as duplicate"))
        self._log_event("duplicate", note=_("Marked as a duplicate by %s.")
                        % self.env.user.name)
        self.message_post(body=_("Marked as a duplicate and cancelled by %s.")
                          % self.env.user.name)
        return True

    def action_close(self):
        for ticket in self:
            if ticket.state != "resolved":
                raise UserError(_("Only resolved tickets can be closed."))
            ticket.write({"closed_at": fields.Datetime.now()})
            ticket._log_event("closed")
            ticket._set_state("closed", note=_("Closed"))
        return True

    def action_reopen(self):
        for ticket in self:
            if ticket.state not in ("resolved", "closed", "cancelled", "rejected"):
                raise UserError(_("This ticket cannot be reopened."))
            ticket.write({"resolved_at": False, "closed_at": False})
            ticket._open_clock("facility", note=_("Reopened"))
            ticket._log_event("reopened")
            ticket._set_state("in_progress", note=_("Reopened"))
            # the team that has to pick it up again must be told
            ticket._mail_reopened()
        return True

    def action_cancel(self):
        for ticket in self:
            ticket._open_lines().action_close("cancelled")
            ticket._set_state("cancelled", note=_("Cancelled"))
        return True

    # ============================================================== helpers
    def _wizard(self, model, name, context):
        """Open a wizard form, pre-filled with the ticket.

        The web client only pre-fills a new record from context keys that start
        with `default_`. Passing `ticket_id` instead of `default_ticket_id` made
        the form open blank and saving died with "a mandatory field is not set".
        """
        defaults = {key if key.startswith("default_") else "default_" + key: value
                    for key, value in (context or {}).items()}
        return {
            "name": name, "type": "ir.actions.act_window", "res_model": model,
            "view_mode": "form", "target": "new", "context": defaults,
        }

    # ============================================================== crons
    @api.model
    def cron_auto_close(self):
        """Close tickets that were resolved and not reopened within the window."""
        days = int(self.env["ir.config_parameter"].sudo().get_param(
            "svcticket.auto_close_days", RESOLVED_AUTO_CLOSE_DAYS) or RESOLVED_AUTO_CLOSE_DAYS)
        cutoff = fields.Datetime.now() - timedelta(days=days)
        tickets = self.search([("state", "=", "resolved"), ("resolved_at", "<=", cutoff)])
        for ticket in tickets:
            ticket.write({"closed_at": fields.Datetime.now()})
            ticket._log_event("closed", note=_("Auto-closed after %s days") % days)
            ticket._set_state("closed", note=_("Auto-closed"))
        return True

    @api.model
    def cron_payment_reminder(self):
        """Nudge the payment team about tickets added for payment but not paid."""
        tickets = self.search([("state", "=", "payment_pending"), ("lane_state", "=", "paused")])
        for ticket in tickets:
            owners = ticket.team_id.approver_ids.mapped("partner_id") or ticket.team_id.mapped(
                "lead_ids.partner_id")
            self.env["svcticket.channel"].notify(
                ticket, owners, _("Payment pending: %s") % ticket.name,
                _("<b>%s</b> is added for payment but nothing has been recorded yet.") % ticket.name)
        return True
