# -*- coding: utf-8 -*-
"""Service Ticket: fields, state list, creation rules, photo enforcement."""
import logging

from odoo import api, fields, models, _
from odoo.tools import table_exists
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# "rejected" is NOT here on purpose: rejecting the whole ticket stops the clock.
# A rejected *hold request* puts the ticket back to in_progress (see lead_reject).
OPEN_STATES = ("new", "acknowledged", "in_progress", "hold_requested", "on_hold",
               "po_pending", "payment_pending")


class SvcTicket(models.Model):
    _name = "svcticket.ticket"
    _description = "Service Ticket"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority_sequence, sla_deadline asc, id desc"
    _rec_name = "name"

    # ------------------------------------------------------------- identity
    name = fields.Char("Ticket #", default="/", readonly=True, copy=False, index=True)
    state = fields.Selection([
        ("pending_approval", "Pending Approval"),
        ("new", "New"),
        ("acknowledged", "Acknowledged"),
        ("in_progress", "In Progress"),
        ("hold_requested", "Hold Requested"),
        ("on_hold", "On Hold"),
        ("po_pending", "With PO Team"),
        ("payment_pending", "With Payment Team"),
        ("rejected", "Rejected"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ], default="new", required=True, tracking=True, index=True,
        group_expand="_expand_states")
    color = fields.Integer(compute="_compute_color")

    location_id = fields.Many2one("svcticket.location", required=True, tracking=True,
                                  index=True, ondelete="restrict")
    category_id = fields.Many2one("svcticket.category", required=True, tracking=True,
                                  domain="[('parent_id','=',False)]", ondelete="restrict")
    subcategory_id = fields.Many2one("svcticket.category", tracking=True, ondelete="restrict",
                                     domain="[('parent_id','=',category_id)]")
    other_issue_text = fields.Char("Other Issue")
    is_other_category = fields.Boolean(compute="_compute_other_flags", store=True)
    is_other_subcategory = fields.Boolean(compute="_compute_other_flags", store=True)
    show_subcategory = fields.Boolean(compute="_compute_other_flags", store=True)
    show_other_text = fields.Boolean(compute="_compute_other_flags", store=True)
    can_set_category = fields.Boolean(
        compute="_compute_can_set_category",
        help="The Issue section is fixed once the ticket is raised. Only a Service "
             "Ticket manager can still correct a wrong category or priority.")
    can_vet_invoice = fields.Boolean(
        compute="_compute_can_vet_invoice",
        help="Only the accounts team that is holding the ticket may accept or "
             "reject the tax invoice.")
    priority_id = fields.Many2one("svcticket.priority", tracking=True, ondelete="restrict")
    priority_sequence = fields.Integer(related="priority_id.sequence", store=True, index=True)
    description = fields.Html(sanitize=True)

    reporter_id = fields.Many2one("res.users", default=lambda self: self.env.user,
                                  required=True, tracking=True, index=True)
    reporter_phone = fields.Char(compute="_compute_reporter_phone", store=True)

    team_id = fields.Many2one("svcticket.team", "Home Team", tracking=True,
                              index=True, ondelete="restrict")
    current_team_id = fields.Many2one(
        "svcticket.team", "Holder Team", tracking=True, index=True, ondelete="restrict",
        help="Set by the routing rules. The assignees stay as they are.")
    current_owner_type = fields.Selection([
        ("team", "A team"),
        ("lead", "The team's lead"),
        ("reporter", "The reporter"),
        ("approver", "Approver"),
        ("user", "A specific user"),
    ], default="team", required=True, tracking=True, index=True)
    current_user_id = fields.Many2one(
        "res.users", "Held By", tracking=True, index=True, ondelete="restrict",
        help="When a person (reporter, director, team lead) holds the ticket "
             "instead of a whole team.")
    current_approver_ids = fields.Many2many(
        "svcticket.approver", "svcticket_ticket_current_approver_rel",
        "ticket_id", "approver_id", "Approval Pending With")
    waiting_on = fields.Char(compute="_compute_waiting_on", store=True)
    prev_owner_type = fields.Selection([
        ("team", "A team"), ("lead", "The team's lead"), ("reporter", "The reporter"),
        ("approver", "Approver"), ("user", "A specific user"),
    ], readonly=True)
    prev_team_id = fields.Many2one("svcticket.team", readonly=True)
    prev_user_id = fields.Many2one("res.users", readonly=True)
    can_act = fields.Boolean(compute="_compute_can_act",
                             help="True when the current user holds the ticket.")
    can_vet_hold = fields.Boolean(compute="_compute_can_act")
    handoff_option_ids = fields.One2many(
        "svcticket.handoff", compute="_compute_handoff_options",
        string="What can happen next")

    # -------------------------------------------------------------- invoice
    invoice_accepted = fields.Boolean(tracking=True,
                                      help="Accounts must accept the tax invoice before "
                                           "the ticket can be resolved.")
    invoice_accepted_at = fields.Datetime(readonly=True)
    invoice_accepted_by = fields.Many2one("res.users", readonly=True)
    invoice_reject_count = fields.Integer(default=0, readonly=True)
    invoice_reject_reason = fields.Text(readonly=True)
    payment_added_at = fields.Datetime(readonly=True, copy=False,
                                       help="When the accounts team picked this up for payment.")
    payment_added_by = fields.Many2one("res.users", readonly=True, copy=False)
    assignee_ids = fields.Many2many("res.users", "svcticket_ticket_assignee_rel",
                                    "ticket_id", "user_id", tracking=True)
    is_duplicate = fields.Boolean("Marked as Duplicate", readonly=True, copy=False,
                                  help="Marked by the category approver when this ticket is a duplicate of "
                                       "another ticket - kept so the audit trail is not erased.")
    duplicate_of = fields.Many2one("svcticket.ticket", "Duplicate Of", readonly=True,
                                   copy=False, ondelete="set null")
    can_mark_duplicate = fields.Boolean(compute="_compute_can_mark_duplicate")

    # ---------------------------------------------------------------- media
    intake_photo_ids = fields.Many2many("ir.attachment", "svcticket_intake_photo_rel",
                                        "ticket_id", "attachment_id", "Issue Photos")
    resolution_photo_ids = fields.Many2many("ir.attachment", "svcticket_resolution_photo_rel",
                                            "ticket_id", "attachment_id", "Resolution Photos")
    cloud_photo_ids = fields.One2many("svcticket.cloud.photo", "ticket_id", "Archived Photos")

    # ------------------------------------------------------------------ SLA
    sla_stage_id = fields.Many2one("svcticket.sla.stage", "SLA Stage", ondelete="set null")
    sla_deadline = fields.Datetime(index=True, tracking=True)
    sla_breached = fields.Boolean(default=False, index=True, tracking=True)
    escalation_level = fields.Integer(default=0)
    active_lane = fields.Selection([
        ("facility", "Facility Team"), ("po", "PO Team"),
        ("approver", "Approver"), ("payment", "Payment Team"),
    ], compute="_compute_lane_state", store=True, index=True)
    lane_state = fields.Selection([
        ("running", "Running"), ("paused", "Paused"), ("none", "No Clock"),
    ], compute="_compute_lane_state", store=True)

    # --------------------------------------------------------------- holds
    hold_reason_id = fields.Many2one("svcticket.hold.reason", tracking=True)
    hold_po_number = fields.Char(tracking=True)
    hold_cost = fields.Float("Estimated Cost", tracking=True)
    hold_needs_po = fields.Boolean("Needs Purchase Order", tracking=True,
                                   help="Set when the technician requests a PO instead of a plain hold.")
    hold_approver_ids = fields.Many2many("svcticket.approver", "svcticket_hold_approver_rel",
                                         "ticket_id", "approver_id", "Requested Approvers")
    hold_note = fields.Text()
    hold_reject_note = fields.Text()
    pre_hold_state = fields.Char("State Before Hold", default="in_progress")
    hold_started_at = fields.Datetime(readonly=True)
    hold_hours = fields.Float("Total Hold (h)", readonly=True)

    # ------------------------------------------------------- money / quotes
    po_ids = fields.One2many("svcticket.po", "ticket_id", "Purchase Orders")
    payment_ids = fields.One2many("svcticket.payment", "ticket_id", "Payments")
    amount_total = fields.Monetary(compute="_compute_amounts", store=True)
    amount_paid = fields.Monetary(compute="_compute_amounts", store=True)
    amount_waived = fields.Monetary("Amount Waived / Written Off", default=0.0,
                                    help="Balance written off when the ticket is closed without full payment. "
                                         "Amount due = approved PO minus payments minus the waived amount, "
                                         "so an audit can see PO 10,000 / paid 9,000 / waived 1,000 / due 0.")
    amount_due = fields.Monetary(compute="_compute_amounts", store=True)
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    invoice_number = fields.Char("Tax Invoice #")
    invoice_attachment_ids = fields.Many2many("ir.attachment", "svcticket_invoice_rel",
                                              "ticket_id", "attachment_id", "Tax Invoices")

    # ------------------------------------------------------------ closing
    resolution_notes = fields.Text()
    draft_message = fields.Html("Quick Message", sanitize=True)
    conversation_log_ids = fields.One2many("svcticket.conversation.log", "ticket_id",
                                           "Ticket Communications")
    acknowledged_at = fields.Datetime(readonly=True)
    started_at = fields.Datetime(readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)

    # ================================================================ helpers
    @api.model
    def _expand_states(self, states, domain, order):
        return [key for key, _label in self._fields["state"].selection]

    @api.depends("sla_breached", "state", "priority_id")
    def _compute_color(self):
        for rec in self:
            if rec.state in OPEN_STATES and rec.sla_breached:
                rec.color = 1
            elif rec.lane_state == "paused":
                rec.color = 3
            else:
                rec.color = rec.priority_id.color or 0

    @api.depends("current_owner_type", "current_team_id", "current_user_id",
                 "current_approver_ids", "reporter_id")
    def _compute_waiting_on(self):
        for rec in self:
            if rec.current_owner_type == "team":
                rec.waiting_on = rec.current_team_id.name or ""
            elif rec.current_owner_type == "lead":
                rec.waiting_on = _("Team Lead - %s") % (rec.current_team_id.name or "")
            elif rec.current_owner_type == "reporter":
                rec.waiting_on = _("Reporter - %s") % (rec.current_user_id.name or rec.reporter_id.name or "")
            elif rec.current_owner_type == "approver":
                # display_name carries the job title, e.g. "R. Sharma (Director)"
                names = rec.current_approver_ids.mapped("display_name") or (
                    [rec.current_user_id.name] if rec.current_user_id else [])
                rec.waiting_on = _("Approval - %s") % ", ".join(filter(None, names))
            else:
                rec.waiting_on = rec.current_user_id.name or ""

    @api.depends("current_owner_type", "current_team_id", "current_user_id",
                 "assignee_ids", "state")
    @api.depends_context("uid")
    def _compute_can_act(self):
        user = self.env.user
        manager = user.has_group("service_ticket.group_manager")
        for rec in self:
            team = rec.current_team_id
            # who HOLDS it decides - being a historical assignee only grants
            # visibility, never the buttons
            if rec.current_owner_type == "approver":
                # the team keeps the ticket but only the approver decides
                holds = user in rec.current_approver_ids.mapped("user_id")
            else:
                holds = bool(
                    rec.current_user_id == user
                    or (team and user in team.member_ids)
                    or (team and user in team.lead_ids)
                )
            rec.can_act = bool(manager or holds)
            rec.can_vet_hold = bool(
                manager or (team and user in team.lead_ids)
                or (rec.current_owner_type == "lead" and rec.current_user_id == user)
            )

    @api.depends("category_id", "subcategory_id")
    @api.depends_context("uid")
    def _compute_can_mark_duplicate(self):
        """Only the approver(s) on the ticket's sub-category (falling back to the
        main category) may mark it a duplicate."""
        user = self.env.user
        for rec in self:
            cat = rec.subcategory_id or rec.category_id
            approvers = cat.approver_ids.mapped("user_id") if cat else self.env["res.users"]
            rec.can_mark_duplicate = bool(user in approvers)

    @api.depends("current_owner_type", "current_team_id", "current_user_id", "state")
    @api.depends_context("uid")
    def _compute_handoff_options(self):
        rows = self.env["svcticket.handoff"]
        for rec in self:
            rec.handoff_option_ids = (rows.available_for(rec).filtered(
                lambda r: r._user_can_run(rec)) if rec.id else rows)

    @api.depends("reporter_id")
    def _compute_reporter_phone(self):
        """Prefer the employee work/mobile number, fall back to the user's phone."""
        Employee = self.env.get("hr.employee")
        for rec in self:
            phone = ""
            if rec.reporter_id:
                if Employee is not None:
                    employee = Employee.sudo().search(
                        [("user_id", "=", rec.reporter_id.id)], limit=1)
                    if employee:
                        phone = employee.work_phone or employee.mobile_phone or ""
                phone = phone or rec.reporter_id.phone or ""
            rec.reporter_phone = phone

    @api.depends("po_ids.amount", "po_ids.state", "payment_ids.amount",
                 "payment_ids.state", "amount_waived")
    def _compute_amounts(self):
        for rec in self:
            pos = rec.po_ids.filtered(lambda p: p.state == "approved")
            rec.amount_total = sum(pos.mapped("amount"))
            rec.amount_paid = sum(rec.payment_ids.filtered(
                lambda p: p.state in ("recorded", "verified")).mapped("amount"))
            deductions = sum(p.deduction_amount for p in rec.payment_ids.filtered(
                lambda p: p.state in ("recorded", "verified")))
            rec.amount_due = max(rec.amount_total - rec.amount_paid
                                 - deductions - rec.amount_waived, 0.0)

    @api.depends("sla_line_ids.state", "sla_line_ids.lane", "sla_line_ids.sequence")
    def _compute_lane_state(self):
        for rec in self:
            # the lead's vetting clock runs beside the team's clock - the ticket
            # always reports the team's lane and its deadline
            open_lines = rec.sla_line_ids.filtered(
                lambda l: l.state in ("running", "paused") and not l.is_vetting)
            current = open_lines.sorted("sequence")[-1:] if open_lines else open_lines
            rec.active_lane = current.lane if current else False
            rec.lane_state = current.state if current else "none"

    # ================================================================ photos
    def _config_category(self):
        """The category whose settings rule this ticket: the sub-category when
        one is chosen, otherwise the main category. (Reading the parent's boxes
        is why a ticked "Genuine Check Required" or a cleared photo requirement
        on the sub-category looked like it was being ignored.)"""
        self.ensure_one()
        return self.subcategory_id or self.category_id

    def _required_photos(self, kind):
        """0 = no photos needed. The sub-category wins, the main category is
        the fallback - so a sub-category can be stricter OR looser."""
        self.ensure_one()
        cat = self._config_category()
        if not cat:
            return 0
        if not cat["require_%s_photo" % kind]:
            return 0
        return cat["min_%s_photos" % kind] or 1

    def _count_photos(self, kind):
        self.ensure_one()
        local = self.intake_photo_ids if kind == "intake" else self.resolution_photo_ids
        cloud = self.cloud_photo_ids.filtered(lambda p: p.kind == kind)
        return len(local) + len(cloud)

    def _check_photos(self, kind):
        for rec in self:
            needed = rec._required_photos(kind)
            have = rec._count_photos(kind)
            if have < needed:
                raise UserError(_(
                    "%(label)s needs at least %(needed)s photo(s). You attached %(have)s."
                ) % {"label": rec.display_name,
                     "needed": needed, "have": have})

    @api.depends("category_id", "category_id.code", "subcategory_id", "subcategory_id.code")
    def _compute_other_flags(self):
        for rec in self:
            cat_other = (rec.category_id.code or "").upper() == "OTHER"
            sub_other = (rec.subcategory_id.code or "").upper() == "OTHER"
            rec.is_other_category = cat_other
            rec.is_other_subcategory = sub_other
            rec.show_subcategory = bool(rec.category_id) and not cat_other
            rec.show_other_text = cat_other or sub_other

    @api.onchange("category_id")
    def _onchange_category(self):
        self.subcategory_id = False
        if self.category_id.default_priority_id:
            self.priority_id = self.category_id.default_priority_id

    @api.onchange("subcategory_id")
    def _onchange_subcategory(self):
        if self.subcategory_id.default_priority_id:
            self.priority_id = self.subcategory_id.default_priority_id

    @api.constrains("other_issue_text", "category_id", "subcategory_id")
    def _check_other_text(self):
        for rec in self:
            if (rec.category_id.code == "OTHER" or rec.subcategory_id.code == "OTHER") \
                    and not (rec.other_issue_text or "").strip():
                raise UserError(_("Please describe the issue in 'Other Issue'."))

    # ================================================================ create
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("svcticket.ticket") or "/"
            if not vals.get("priority_id"):
                cat = self.env["svcticket.category"].browse(vals.get("category_id")).exists()
                sub = self.env["svcticket.category"].browse(vals.get("subcategory_id")).exists()
                vals["priority_id"] = (
                        (sub.default_priority_id or cat.default_priority_id).id
                        or self._fallback_priority().id
                )
        tickets = super().create(vals_list)
        tickets._check_photos("intake")
        for ticket in tickets:
            ticket._auto_assign_team()
            if not ticket.current_team_id:
                # holder = home team, so "Home Team" and "Holder Team" always
                # agree when the ticket is raised
                ticket.current_team_id = ticket.team_id or ticket._team_for_lane("facility")
            ticket.current_owner_type = "team"
            if ticket._config_category().requires_approval:
                ticket.state = "pending_approval"
                # The approver is the holder until they decide. Without this the
                # ticket shows in nobody's queue: it is not on the team's desk
                # (they cannot start) and current_approver_ids stays empty, so
                # "My Tickets" and "Waiting on Me" both miss it.
                ticket.write({
                    "current_owner_type": "approver",
                    "current_approver_ids": [(6, 0, ticket._approval_approvers().ids)],
                })
                ticket._mail_genuine_check()
            ticket._open_clock("facility", seconds=86400.0)
            ticket._log_event("created", note=ticket.state)
            if not ticket._config_category().requires_approval:
                # a genuine-check ticket goes to its approver instead (event 2)
                ticket._mail_raised()
            ticket._broadcast()
            ticket._own_attachments(ticket.intake_photo_ids)
            ticket._offload_photos("intake")
        return tickets

    @api.depends("team_id", "current_team_id", "state")
    @api.depends_context("uid")
    def _compute_can_set_category(self):
        """The Issue section is read-only from the moment the ticket is raised.

        Category and priority decide the SLA stages and the routed teams, so a
        ticket that changes them halfway rewrites its own history. Nobody in the
        flow may edit them any more; a manager can still fix a genuine mistake
        from the form (or by import) when the reporter picked the wrong one.
        """
        manager = self.env.user.has_group("service_ticket.group_manager")
        for rec in self:
            rec.can_set_category = manager

    @api.depends("current_owner_type", "current_team_id", "state")
    @api.depends_context("uid")
    def _compute_can_vet_invoice(self):
        """Accepting or rejecting a tax invoice belongs to the accounts team only."""
        user = self.env.user
        for rec in self:
            team = rec.current_team_id
            rec.can_vet_invoice = bool(
                rec.current_owner_type == "team" and team
                and team.team_type == "account_team"
                and (user in team.member_ids or user in team.lead_ids))

    ROUTING_FIELDS = ("category_id", "subcategory_id", "priority_id")

    def write(self, vals):
        needs_recalc = (bool(set(self.ROUTING_FIELDS) & set(vals))
                        and not self.env.context.get("svcticket_no_recalc"))
        if needs_recalc:
            for ticket in self:
                if not ticket.can_set_category:
                    raise UserError(_(
                        "The Issue section of %s is fixed: the category and the "
                        "priority cannot be changed after the ticket is raised. "
                        "Ask a Service Ticket manager if the wrong one was picked.")
                                    % ticket.name)
        res = super().write(vals)
        if "resolution_photo_ids" in vals:
            for ticket in self:
                ticket._own_attachments(ticket.resolution_photo_ids)
            self._offload_photos("resolution")
        if needs_recalc:
            self._recalc_sla_after_change()
        return res

    def _recalc_sla_after_change(self):
        """Category -> its default priority -> SLA stages. Whatever changed, the
        open clock is restarted on the right stage with a fresh full limit."""
        for ticket in self:
            if ticket.state not in OPEN_STATES:
                continue
            cat = ticket.subcategory_id or ticket.category_id
            wanted = cat.default_priority_id or ticket.category_id.default_priority_id
            if wanted and wanted != ticket.priority_id:
                ticket.with_context(svcticket_no_recalc=True).write(
                    {"priority_id": wanted.id})
            lines = ticket._open_lines()
            lane = lines[:1].lane or "facility"
            paused = bool(lines) and lines[:1].state == "paused"
            for line in lines:
                line.action_close("recalculated")
            stage = ticket._first_stage()
            ticket._open_clock(
                lane, start_paused=paused,
                seconds=stage._seconds() if stage else None,
                note=_("Recalculated"))
            ticket.message_post(body=_(
                "Category / priority set to <b>%s</b> / <b>%s</b> by %s. "
                "The SLA clock restarted on %s (deadline %s).") % (
                                         (ticket.subcategory_id or ticket.category_id).display_name,
                                         ticket.priority_id.display_name or "-",
                                         self.env.user.name,
                                         stage.name if stage else _("the first stage"),
                                         fields.Datetime.to_string(ticket.sla_deadline) or _("paused")))

    @api.model
    def _fallback_priority(self):
        Priority = self.env["svcticket.priority"]
        return (Priority.search([("code", "=", "UNK")], limit=1)
                or Priority.search([], order="sequence", limit=1))

    def _auto_assign_team(self):
        """Every ticket starts at a FACILITY team - that team is both the home
        team and the first holder. PO / accounts teams are reached by routing
        later, never by assigning them here (that is how a ticket ended up with
        "Home Team: Account Team" while the facility team was holding it)."""
        self.ensure_one()
        if self.team_id:
            return
        Team = self.env["svcticket.team"]
        cat = self._config_category()
        facility_match = Team.browse()
        facility_any = Team.browse()
        other_match = Team.browse()
        for team in Team.search([("active", "=", True)]):
            if team.location_ids and self.location_id not in team.location_ids:
                continue
            parent = cat.parent_id
            matches = (cat in team.subcategory_ids
                       or cat in team.category_ids
                       or (parent and parent in team.category_ids))
            if team.team_type == "facility":
                if matches and not facility_match:
                    facility_match = team
                if not facility_any:
                    facility_any = team
            elif matches and not other_match:
                other_match = team
        self.team_id = facility_match or facility_any or other_match

    # ============================================================ messaging
    # ========================================================== S3 archival
    def _own_attachments(self, attachments):
        """Keep every document ON the ticket.

        Odoo checks `ir.attachment` access through the record the file is
        attached to. Files uploaded in a wizard (or left without a record) are
        only readable by the person who uploaded them, so the next team got
        "doesn't have 'read' access to: Attachment". Re-homing them onto the
        ticket makes the document follow the ticket's own permissions.
        """
        self.ensure_one()
        if not attachments:
            return attachments
        strays = attachments.sudo().filtered(
            lambda a: a.res_model != "svcticket.ticket" or a.res_id != self.id)
        if strays:
            strays.write({"res_model": "svcticket.ticket", "res_id": self.id})
        return attachments

    @api.model
    def _auto_init(self):
        """Run the document repair once per build - on install AND on upgrade.

        (`<function>` in a noupdate data block only runs at install time, so an
        existing database would keep its unreadable attachments forever.)
        """
        res = super()._auto_init()
        ICP = self.env["ir.config_parameter"].sudo()
        marker = "svcticket.attachment_repair_v2"
        # on a fresh install our own tables may not exist yet - skip silently
        tables_ready = all(table_exists(self.env.cr, self.env[m]._table)
                           for m in ("svcticket.payment", "svcticket.po"))
        if tables_ready and ICP.get_param(marker) != "1":
            try:
                with self.env.cr.savepoint():
                    self._repair_attachment_links()
                    ICP.set_param(marker, "1")
            except Exception:  # never block an install/upgrade
                _logger.exception("attachment repair failed")
        return res

    def _open_vetting_clock_for_hold(self, row):
        """Convenience used by the hold wizard."""
        self.ensure_one()
        return self._open_vetting_clock(
            row._seconds(self) if row else 0.0,
            reason=_("Hold approval (%s)") % (row.name if row else ""))

    @api.model
    def _repair_attachment_links(self):
        """Upgrade step: re-home documents saved by older builds."""
        Ticket = self.sudo()
        for field in ("intake_photo_ids", "resolution_photo_ids",
                      "invoice_attachment_ids"):
            for ticket in Ticket.search([(field, "!=", False)]):
                ticket._own_attachments(ticket[field])
        for payment in self.env["svcticket.payment"].sudo().search(
                [("proof_ids", "!=", False)]):
            payment.ticket_id._own_attachments(payment.proof_ids)
        for po in self.env["svcticket.po"].sudo().search(
                [("attachment_ids", "!=", False)]):
            po.ticket_id._own_attachments(po.attachment_ids)
        return True

    def _offload_photos(self, kind):
        """Push to S3 (if enabled) and drop the local attachment."""
        from .s3_storage import upload_attachments
        for ticket in self:
            attachments = (ticket.intake_photo_ids if kind == "intake"
                           else ticket.resolution_photo_ids)
            if not attachments:
                continue
            uploaded, _failed = upload_attachments(ticket.env, ticket, attachments, kind)
            if uploaded:
                ticket.message_post(body=_("%s photo(s) archived to cloud storage.") % uploaded)

    @api.model
    def cron_daily_digest(self):
        """Daily digest: one mail per person holding open tickets."""
        open_tickets = self.sudo().search([("state", "in", list(OPEN_STATES))])
        by_user = {}
        for ticket in open_tickets:
            holders = self.env["res.users"]
            if ticket.current_owner_type == "user" and ticket.current_user_id:
                holders = ticket.current_user_id
            elif ticket.current_owner_type == "team" and ticket.current_team_id:
                holders = ticket.current_team_id.member_ids | ticket.current_team_id.lead_ids
            elif ticket.current_owner_type == "lead" and ticket.current_user_id:
                holders = ticket.current_user_id
            for user in holders:
                if not user.partner_id or not user.partner_id.email:
                    continue
                by_user.setdefault(user, self.env["svcticket.ticket"])
                by_user[user] |= ticket
        for user, tickets in by_user.items():
            rows = []
            state_counts = {}
            for t in tickets.sorted(key=lambda t: t.name):
                label = dict(t._fields["state"].selection).get(t.state, t.state)
                state_counts[t.state] = state_counts.get(t.state, 0) + 1
                rows.append((t.name, label))
            summary = ", ".join("%s: %d" % (k, v) for k, v in sorted(state_counts.items()))
            intro = _("You hold %d open ticket%s (%s).") % (
                len(tickets), "s" if len(tickets) > 1 else "", summary)
            body = (
                    "<div style='font-family:Arial,sans-serif;font-size:14px;color:#222'>"
                    "<h3 style='margin:0 0 6px'>Daily digest - %s</h3>"
                    "<p style='margin:0 0 8px'>%s</p>"
                    % (fields.Date.context_today(self), intro))
            if rows:
                cells = "".join(
                    "<tr><td style='padding:6px 10px;border:1px solid #e3e3e3'>%s</td>"
                    "<td style='padding:6px 10px;border:1px solid #e3e3e3'><b>%s</b></td></tr>"
                    % (name, state) for name, state in rows)
                body += "<table style='border-collapse:collapse;margin:8px 0'>%s</table>" % cells
            body += "</div>"
            tickets[0].sudo()._send_mail(
                tickets[0], user.partner_id,
                _("Daily digest: your open tickets (%s)") % fields.Date.context_today(self),
                body)
        return True


class SvcTicketConversationLog(models.Model):
    """Ticket Communications tab - every posted update and internal note."""
    _name = "svcticket.conversation.log"
    _description = "Service Ticket Conversation History"
    _order = "create_date desc"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, ondelete="cascade", index=True)
    author_id = fields.Many2one("res.users", default=lambda self: self.env.user, required=True)
    message = fields.Html(required=True, sanitize=True)
    is_internal = fields.Boolean("Internal Note", default=False)


class SvcTicketCloudPhoto(models.Model):
    _name = "svcticket.cloud.photo"
    _description = "Photo Archived on S3"
    _order = "id desc"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, ondelete="cascade", index=True)
    kind = fields.Selection([
        ("intake", "Issue"), ("resolution", "Resolution"),
        ("payment", "Payment Proof"), ("invoice", "Tax Invoice"),
    ], required=True, default="intake")
    filename = fields.Char(required=True)
    url = fields.Char("S3 URL", required=True)
