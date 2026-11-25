# -*- coding: utf-8 -*-
"""Config-driven routing.

One row = one hop: "when the ticket is with X and someone clicks Y, give it to Z
and run or pause the clock". Nothing about the facility flow is hardcoded here.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

# what a ticket can be held by (used on the ticket itself)
OWNER_TYPES = [
    ("team", "A team"),
    ("lead", "The team's lead"),
    ("reporter", "The reporter"),
    ("approver", "Approver"),
    ("user", "A specific user"),
]

# where a routing rule can apply FROM
FROM_TYPES = [
    ("any", "Anyone holding it"),
    ("team", "One particular team"),
    ("team_type", "Any team of a given type"),
    ("lead", "A team lead"),
    ("reporter", "The reporter"),
    ("approver", "An approver"),
    ("user", "One particular user"),
]

# where a routing rule can send TO
TO_TYPES = [
    ("team", "One particular team"),
    ("team_type", "Any team of a given type"),
    ("lead", "That team's lead"),
    ("reporter", "The reporter"),
    ("approver", "Approver(s) from the registry"),
    ("user", "One particular user"),
    ("previous_owner", "Whoever held it before"),
]

TEAM_TYPES = [
    ("facility", "Facility Team"),
    ("po_team", "PO Team"),
    ("account_team", "Accounts Team"),
    ("other", "Other"),
]


class SvcTicketHandoff(models.Model):
    _name = "svcticket.handoff"
    _description = "Handoff Routing Rule"
    _order = "sequence, id"

    name = fields.Char("Button Label", required=True,
                       help="The text on the button, e.g. Send for Director Approval.")
    code = fields.Char(help="Short stable key, e.g. raise_po. Optional.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------ from
    from_owner_type = fields.Selection(
        FROM_TYPES, default="team", required=True, string="Appears when held by",
        help="Only this holder sees the button.")
    from_team_id = fields.Many2one("svcticket.team", "Current team")
    from_team_type = fields.Selection(TEAM_TYPES, "Current team type")
    from_user_id = fields.Many2one("res.users", "Current user")

    # -------------------------------------------------------------------- to
    to_owner_type = fields.Selection(TO_TYPES, default="team", required=True,
                                     string="Moves the ticket to")
    to_team_id = fields.Many2one("svcticket.team", "Next team")
    to_team_type = fields.Selection(TEAM_TYPES, "Next team type")
    to_user_id = fields.Many2one("res.users", "Next user")
    to_approver_ids = fields.Many2many(
        "svcticket.approver", "svcticket_handoff_approver_rel",
        "handoff_id", "approver_id", "Approvers to choose from")

    # ----------------------------------------------------------------- clock
    lane = fields.Selection([
        ("facility", "Facility Team"),
        ("po", "PO Team"),
        ("approver", "Approver"),
        ("payment", "Accounts Team"),
    ], default="facility", required=True, string="SLA Lane",
        help="Which SLA clock opens. It is only a label - the stages and "
             "limits still come from the ticket's priority.")
    clock_policy = fields.Selection([
        ("run", "Run - the holder owes us action"),
        ("pause", "Pause - we are waiting on the owner, not the team"),
    ], default="run", required=True, string="Clock Policy")
    time_limit = fields.Float("Default Limit")
    time_unit = fields.Selection([
        ("minutes", "Minutes"), ("hours", "Hours"), ("days", "Days"),
    ], default="hours")
    limit_ids = fields.One2many("svcticket.handoff.limit", "handoff_id",
                                "Per-Priority Limits")

    # ------------------------------------------------------------------ misc
    sets_state = fields.Selection([
        ("pending_approval", "Pending Approval"),
        ("new", "New"),
        ("acknowledged", "Acknowledged"),
        ("in_progress", "In Progress"),
        ("hold_requested", "Hold Requested"),
        ("on_hold", "On Hold"),
        ("rejected", "Rejected"),
        ("po_pending", "With PO Team"),
        ("payment_pending", "With Accounts Team"),
        ("resolved", "Resolved"),
    ], help="Move the ticket to this status when the row runs. Leave empty to keep it.")
    approves_po = fields.Boolean(
        "Also approves any PO waiting for approval",
        help="Use when the director said yes outside Odoo (on a call). "
             "The PO is marked approved so amounts and invoices work.")
    run_action = fields.Char(
        "Runs this ticket action",
        help="Technical name of the ticket method this row must call instead of "
             "moving the ticket blindly, e.g. action_record_payment. Needed "
             "whenever the step has to open a wizard or validate something - "
             "otherwise the Next Step list would skip the form and silently "
             "change the ticket.")
    show_in_next_step = fields.Boolean(
        "Show in the Next Step list", default=True,
        help="Untick when the same movement already has a button at the top of "
             "the ticket (Approve Hold / Reject Hold).")
    ask_note = fields.Boolean("Ask for a Note")
    note_label = fields.Char(default="Note")
    is_default = fields.Boolean("Ships with the module", readonly=True)
    from_label = fields.Char(compute="_compute_labels")
    to_label = fields.Char(compute="_compute_labels")
    summary = fields.Char("Rule", compute="_compute_labels")

    @api.depends("name", "from_owner_type", "from_team_id", "from_team_type",
                 "to_owner_type", "to_team_id", "to_team_type", "to_approver_ids",
                 "clock_policy", "time_limit", "time_unit")
    def _compute_labels(self):
        for rec in self:
            src = rec._target_label(rec.from_owner_type, rec.from_team_id,
                                    rec.from_team_type, rec.from_user_id, True)
            dst = rec._target_label(rec.to_owner_type, rec.to_team_id,
                                    rec.to_team_type, rec.to_user_id, False,
                                    rec.to_approver_ids)
            rec.from_label = src
            rec.to_label = dst
            limit = ""
            if rec.time_limit:
                limit = " / %g %s" % (rec.time_limit, rec.time_unit or "hours")
            rec.summary = "%s  ->  %s  (%s%s)" % (
                src, dst, "run" if rec.clock_policy == "run" else "pause", limit)

    def _target_label(self, owner_type, team, team_type, user, is_from, approvers=None):
        if owner_type == "any":
            return "Anyone"
        if owner_type in ("team", "team_type"):
            if team:
                return team.name
            if team_type:
                return dict(TEAM_TYPES).get(team_type, team_type)
            return "any team" if is_from else "any team of that type"
        if owner_type == "lead":
            return "team lead"
        if owner_type == "reporter":
            return "the reporter"
        if owner_type == "approver":
            if approvers:
                return ", ".join(approvers.mapped("user_id.name")) or "approver"
            return "approver"
        if owner_type == "user":
            return user.name or "a user"
        if owner_type == "previous_owner":
            return "whoever held it before"
        return owner_type or "-"

    # ------------------------------------------------------------- helpers
    def _matches(self, ticket):
        """Can this row be used on this ticket right now?"""
        self.ensure_one()
        from_type = self.from_owner_type
        # "a team" and "a team of a type" both mean: the holder is a team
        if from_type == "team_type":
            from_type = "team"
        if from_type != "any" and from_type != ticket.current_owner_type:
            return False
        if self.from_owner_type in ("team", "team_type"):
            if self.from_team_id and self.from_team_id != ticket.current_team_id:
                return False
            if self.from_team_type and \
                    ticket.current_team_id.team_type != self.from_team_type:
                return False
        if self.from_owner_type == "user" and self.from_user_id and \
                self.from_user_id != ticket.current_user_id:
            return False
        # a button that sends it to the same place is pointless
        if self.to_owner_type == ticket.current_owner_type == "reporter":
            return False
        return True

    @api.depends_context("uid")
    def _user_can_run(self, ticket):
        """Only the holder (or a manager) may press the button."""
        self.ensure_one()
        user = self.env.user
        if user.has_group("service_ticket.group_manager"):
            return True
        if ticket.current_owner_type == "approver":
            return user in ticket.current_approver_ids.mapped("user_id")
        if ticket.current_user_id:
            return ticket.current_user_id == user
        team = ticket.current_team_id
        return bool(team and (user in team.member_ids or user in team.lead_ids))

    @api.model
    def available_for(self, ticket, for_next_step=True):
        rows = self.search([("active", "=", True)])
        if for_next_step:
            rows = rows.filtered(lambda r: r.show_in_next_step)
        return rows.filtered(lambda r: r._matches(ticket))

    def _seconds(self, ticket):
        """Limit for this hop, per-priority override first."""
        self.ensure_one()
        limit, unit = self.time_limit, self.time_unit or "hours"
        override = self.limit_ids.filtered(
            lambda l: l.priority_id == ticket.priority_id)[:1]
        if override:
            limit, unit = override.time_limit, override.time_unit or unit
        if not limit:
            return None
        return float(limit) * {"minutes": 60.0, "hours": 3600.0,
                               "days": 86400.0}.get(unit, 3600.0)

    @api.model
    def _by_code(self, code):
        row = self.with_context(active_test=False).search([("code", "=", code)], limit=1)
        if not row:
            raise UserError(_(
                "No routing rule with code '%s' is configured. "
                "Configuration > Handoff Routing.") % code)
        return row

    # ------------------------------------------------------------- running
    def _perform(self, ticket, note=None, approvers=None):
        """Close the current clock, move the holder, open or pause the next clock."""
        self.ensure_one()
        ticket.ensure_one()

        team = self.env["svcticket.team"]
        user = self.env["res.users"]
        approver_recs = self.env["svcticket.approver"]

        if self.to_owner_type == "previous_owner":
            team = ticket.prev_team_id
            user = ticket.prev_user_id
        elif self.to_owner_type == "team":
            team = self.to_team_id
        elif self.to_owner_type == "team_type":
            team = self.env["svcticket.team"].search(
                [("team_type", "=", self.to_team_type), ("active", "=", True)],
                limit=1)
            if not team:
                raise UserError(_(
                    "No active team is set as '%s'. Configuration > Teams.")
                    % dict(TEAM_TYPES).get(self.to_team_type, self.to_team_type))
        elif self.to_owner_type == "lead":
            team = ticket.current_team_id or ticket.team_id
            user = team.lead_ids[:1]
            if not user:
                raise UserError(_("Team %s has no team lead set.") % team.name)
        elif self.to_owner_type == "reporter":
            user = ticket.reporter_id
        elif self.to_owner_type == "approver":
            approver_recs = approvers or self.to_approver_ids
            if not approver_recs:
                raise UserError(_("No approvers configured for '%s'.") % self.name)
            user = approver_recs.mapped("user_id")[:1]
        elif self.to_owner_type == "user":
            user = self.to_user_id

        if self.ask_note and not (note or "").strip():
            raise UserError(_("%s needs a %s.") % (self.name, (self.note_label or "note").lower()))

        # 1. same holder, same lane -> just start or stop the existing clock.
        #    Opening a second clock for "Added for Payment" used to leave two
        #    payment lines on the ticket and reset the time already spent.
        # 1. a clock belongs to its LANE. Moving a ticket inside the same lane
        #    (facility -> its lead, lead -> back to the team, team -> reporter)
        #    only starts or stops that clock - it never restarts it, so nobody
        #    gains time by asking for a hold, and nobody loses the time already
        #    spent. A new clock starts only when the ticket changes lane
        #    (facility -> PO -> accounts -> facility).
        open_lines = ticket._open_lines()
        existing = open_lines.filtered(lambda l: l.lane == self.lane)[:1]
        if existing:
            if self.clock_policy == "pause" and existing.state == "running":
                existing.action_pause(_("Waiting on %s")
                                      % (ticket.waiting_on or _("the owner")))
            elif self.clock_policy == "run" and existing.state == "paused":
                existing.action_resume(_("%s resumed") % self.name)
            self._move_holder(ticket, team, user, approver_recs)
            if self.sets_state:
                ticket.sudo().write({"state": self.sets_state})
            self._settle_po(ticket)
            ticket.sudo().message_post(body=_(
                "<b>%s</b> went to <b>%s</b> (%s clock).") % (
                ticket.name, ticket.waiting_on or _("next step"),
                _("running") if existing.state == "running" else _("paused")))
            ticket._log_event("handoff", to_lane=self.lane,
                              note="%s%s" % (self.name,
                                             (" - " + note) if note else ""))
            return True

        # 2. the ticket changes lane -> close the old clock
        for line in open_lines:
            line.action_close("handoff")

        # Everything below writes the ticket *after* it has changed hands, so the
        # acting user is often not allowed to write it any more (record rules).
        # Permission was already checked in action_run -> mechanical writes run
        # as sudo and keep the real user as the author of the chatter messages.
        ticket = ticket.sudo()

        # 3. move the holder (assignees are never touched)
        self._move_holder(ticket, team, user, approver_recs)
        if self.sets_state:
            ticket.sudo().write({"state": self.sets_state})

        # 4. the clock
        if self.clock_policy == "pause":
            line = self.env["svcticket.sla.line"].sudo().open_for(
                ticket, self.lane, seconds=self._seconds(ticket))
            line.action_pause(_("Waiting on %s") % (ticket.waiting_on or "the owner"))
        else:
            self.env["svcticket.sla.line"].sudo().open_for(
                ticket, self.lane, seconds=self._seconds(ticket))

        # 5. settle any PO that was waiting for a decision
        self._settle_po(ticket)

        # 6. tell the story
        ticket.message_post(body=_(
            "<b>%s</b> went to <b>%s</b> (%s clock).") % (
            ticket.name, ticket.waiting_on or _("next step"),
            _("running") if self.clock_policy == "run" else _("paused")))
        ticket._log_event("handoff", to_lane=self.lane,
                          note="%s%s" % (self.name, (" - " + note) if note else ""))
        return True

    def _move_holder(self, ticket, team, user, approver_recs):
        """Write who holds the ticket. Assignees and the home team never move."""
        ticket = ticket.sudo()
        if self.to_owner_type == "previous_owner":
            new_type = ticket.prev_owner_type or "team"
        elif self.to_owner_type in ("team", "team_type"):
            new_type = "team"
        else:
            new_type = self.to_owner_type
        vals = {
            "prev_owner_type": ticket.current_owner_type,
            "prev_team_id": ticket.current_team_id.id or False,
            "prev_user_id": ticket.current_user_id.id or False,
            "current_owner_type": new_type,
            "current_team_id": team.id or False,
            "current_user_id": user.id or False,
        }
        if self.to_owner_type == "approver":
            # the director decides, but the team keeps the ticket (locked flow:
            # "PO team stays, director named") - so the lane does not change hands
            vals["current_approver_ids"] = [(6, 0, approver_recs.ids)]
            vals["current_owner_type"] = "approver"
            vals["current_team_id"] = ticket.current_team_id.id or False
            vals["current_user_id"] = False
        elif new_type in ("reporter", "user", "lead"):
            vals["current_approver_ids"] = [(5, 0, 0)]
        ticket.write(vals)
        return True

    def _settle_po(self, ticket):
        """Rows flagged with `approves_po` also approve the waiting PO."""
        if not self.approves_po:
            return False
        ticket = ticket.sudo()
        for po in ticket.po_ids.filtered(lambda p: p.state in ("draft", "pending")):
            po.sudo().write({
                "state": "approved",
                "decided_by": self.env.user.id,
                "decided_at": fields.Datetime.now(),
            })
            ticket._log_event("po_approved", note="%s (recorded by %s)"
                              % (po.name, self.env.user.name))
        return True

    def action_run(self):
        """Opened from the ticket form: ask for a note if needed, then run."""
        self.ensure_one()
        ticket = self.env["svcticket.ticket"].browse(
            self.env.context.get("svcticket_ticket_id")
            or self.env.context.get("active_id"))
        if not ticket:
            raise UserError(_("Open the ticket first."))
        if not self._matches(ticket):
            raise UserError(_("'%s' cannot be used on this ticket right now.") % self.name)
        if not self._user_can_run(ticket):
            raise UserError(_(
                "This ticket is with %s. Only they can use this button.")
                % (ticket.waiting_on or _("another team")))
        # some steps are not a simple hand-off: they must run their real action
        # (open the payment / invoice form, ask for the reason, ...). Running
        # them straight from here used to skip the form completely.
        if self.run_action:
            method = getattr(ticket, self.run_action, None)
            if method is None:
                raise UserError(_(
                    "'%s' is set to run '%s', which does not exist on the ticket. "
                    "Configuration > Handoff Routing.") % (self.name, self.run_action))
            return method()
        if self.ask_note or self.to_owner_type == "approver":
            return {
                "type": "ir.actions.act_window",
                "name": self.name,
                "res_model": "svcticket.handoff.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {
                    "default_handoff_id": self.id,
                    "default_ticket_id": ticket.id,
                },
            }
        self._perform(ticket)
        self._mail_after_run(ticket)
        return True

    def _mail_after_run(self, ticket):
        """Rows that ask the reporter for something mail them automatically."""
        if self.code in ("request_docs", "docs_submitted"):
            code = self.code
            return (ticket._mail_docs_requested() if code == "request_docs"
                    else ticket._mail_docs_submitted())
        return False


class SvcTicketHandoffLimit(models.Model):
    _name = "svcticket.handoff.limit"
    _description = "Per-Priority Limit for a Routing Rule"
    _order = "priority_id"

    handoff_id = fields.Many2one("svcticket.handoff", required=True, ondelete="cascade")
    priority_id = fields.Many2one("svcticket.priority", required=True, ondelete="cascade")
    time_limit = fields.Float(required=True, default=1.0)
    time_unit = fields.Selection([
        ("minutes", "Minutes"), ("hours", "Hours"), ("days", "Days"),
    ], default="hours", required=True)


class SvcTicketHandoffWizard(models.TransientModel):
    _name = "svcticket.handoff.wizard"
    _description = "Run a Routing Rule"

    handoff_id = fields.Many2one("svcticket.handoff", required=True, readonly=True)
    ticket_id = fields.Many2one("svcticket.ticket", required=True, readonly=True)
    name = fields.Char(related="handoff_id.name")
    ask_note = fields.Boolean(related="handoff_id.ask_note")
    note_label = fields.Char(related="handoff_id.note_label")
    note = fields.Text("Note")
    approver_ids = fields.Many2many(
        "svcticket.approver", string="Send To",
        compute="_compute_approvers", store=True, readonly=False,
        help="From the approver registry.")

    @api.depends("handoff_id")
    def _compute_approvers(self):
        for wizard in self:
            wizard.approver_ids = wizard.handoff_id.to_approver_ids

    def action_confirm(self):
        self.ensure_one()
        approvers = self.approver_ids if self.handoff_id.to_owner_type == "approver" else None
        if self.handoff_id.ask_note and not (self.note or "").strip():
            raise UserError(_("Please write a %s.") % (self.note_label or "note").lower())
        self.handoff_id._perform(self.ticket_id, note=self.note, approvers=approvers)
        return {"type": "ir.actions.act_window_close"}
