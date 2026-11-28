# -*- coding: utf-8 -*-
"""One SLA clock per lane. This is the whole "New SLA Start" mechanic.

facility -> po -> approver -> payment -> back to facility ...
Each hand-off closes the old clock and opens a new one.
"""
import logging

from odoo import api, fields, models, _

from .sla_math import WorkWindow, add_working_seconds, working_seconds_between

_logger = logging.getLogger(__name__)

LANES = [("facility", "Facility Team"), ("po", "PO Team"),
         ("approver", "Approver"), ("payment", "Payment Team"),
         ("lead", "Team Lead")]
LANE_LABELS = dict(LANES)

_WINDOW_CACHE = {}


def team_window(team):
    """Cached per-team work window (recomputed only when the config changes)."""
    if not team:
        return WorkWindow.always_on()
    window = WorkWindow.from_team(team)
    cache_key = (team.id, window.key)
    if cache_key not in _WINDOW_CACHE:
        if len(_WINDOW_CACHE) > 256:
            _WINDOW_CACHE.clear()
        _WINDOW_CACHE[cache_key] = window
    return _WINDOW_CACHE[cache_key]


class SvcTicketSlaLine(models.Model):
    _name = "svcticket.sla.line"
    _description = "Ticket SLA Clock (one per lane)"
    _order = "ticket_id, sequence, id"

    ticket_id = fields.Many2one("svcticket.ticket", required=True, ondelete="cascade", index=True)
    lane = fields.Selection(LANES, required=True, default="facility", index=True)
    stage_id = fields.Many2one("svcticket.sla.stage", "Stage", ondelete="set null")
    stage_name = fields.Char(related="stage_id.name", store=True)
    sequence = fields.Integer(default=10)
    timer_type = fields.Selection([
        ("24_7", "24/7"), ("business_hours", "Business Hours"),
    ], default="business_hours", required=True)

    started_at = fields.Datetime(default=fields.Datetime.now, required=True)
    deadline = fields.Datetime(index=True)
    paused_at = fields.Datetime(readonly=True)
    remaining_seconds = fields.Float("Seconds Owed", readonly=True,
                                     help="Working seconds left when the clock was paused.")
    is_vetting = fields.Boolean(
        "Lead vetting clock", readonly=True,
        help="A second clock that runs NEXT TO the team's clock while a hold "
             "waits for the team lead. It never moves the ticket's own deadline.")
    state = fields.Selection([
        ("running", "Running"), ("paused", "Paused"),
        ("done", "Done"), ("breached", "Breached"),
    ], default="running", required=True, index=True)
    breach_count = fields.Integer(default=0)
    last_notified_at = fields.Datetime(readonly=True)
    closed_at = fields.Datetime(readonly=True)

    # ------------------------------------------------------------- helpers
    def _window(self):
        self.ensure_one()
        return (WorkWindow.always_on() if self.timer_type == "24_7"
                else team_window(self.ticket_id.current_team_id or self.ticket_id.team_id))

    @staticmethod
    def _seconds(stage):
        return stage._seconds() if stage else 0.0

    def _sync_ticket(self):
        """Mirror the open MAIN clock onto the ticket so every view/search keeps
        working. Vetting clocks run next to it and are ignored here."""
        for ticket in self.mapped("ticket_id"):
            open_lines = ticket.sla_line_ids.filtered(
                lambda l: l.state in ("running", "paused") and not l.is_vetting)
            running = open_lines.filtered(lambda l: l.state == "running")[:1]
            current = running or open_lines.sorted("sequence")[-1:]
            if running:
                deadline = running.deadline
            elif current and current.state == "paused":
                # Paused clock: keep a deadline on the ticket instead of leaving
                # it blank - the moment work resumes, this is where it lands.
                deadline = add_working_seconds(
                    fields.Datetime.now(), current.remaining_seconds or 0.0,
                    current._window())
            else:
                deadline = False
            ticket.write({
                "sla_deadline": deadline,
                "sla_stage_id": current.stage_id.id or False,
            })

    # ------------------------------------------------------------- actions
    def action_pause(self, reason="paused"):
        now = fields.Datetime.now()
        for line in self:
            if line.state != "running":
                continue
            owed = (working_seconds_between(now, line.deadline, line._window())
                    if line.deadline else (line.remaining_seconds or 0.0))
            line.write({"state": "paused", "paused_at": now,
                        "remaining_seconds": owed, "deadline": False})
            line.ticket_id._log_event("sla_pause", from_lane=line.lane, note=reason)
        self._sync_ticket()

    def action_resume(self, reason="resumed"):
        now = fields.Datetime.now()
        for line in self:
            if line.state != "paused":
                continue
            line.write({"state": "running", "paused_at": False,
                        "deadline": add_working_seconds(now, line.remaining_seconds,
                                                        line._window())})
            line.ticket_id._log_event("sla_resume", to_lane=line.lane, note=reason)
        self._sync_ticket()

    def action_close(self, reason="done"):
        for line in self:
            if line.state == "done":
                continue
            line.write({"state": "done", "closed_at": fields.Datetime.now(), "deadline": False})
        self._sync_ticket()

    @api.model
    def open_for(self, ticket, lane, stage=None, start_paused=False, seconds=None):
        stage = stage or ticket._first_stage()
        timer_type = ticket.priority_id.timer_type if ticket.priority_id else "business_hours"
        window = (WorkWindow.always_on() if timer_type == "24_7"
                  else team_window(ticket.current_team_id or ticket.team_id))
        last_seq = max(ticket.sla_line_ids.mapped("sequence") or [0])

        # Category override timing: check category/subcategory first
        effective_seconds = seconds
        if effective_seconds is None and stage and ticket.priority_id:
            cat = ticket.subcategory_id or ticket.category_id
            if cat:
                timing = cat.get_sla_timing(ticket.priority_id, stage.level)
                if timing:
                    effective_seconds = float(timing[0]) * {
                        "minutes": 60.0, "hours": 3600.0, "days": 86400.0}.get(timing[1], 3600.0)
        if effective_seconds is None:
            effective_seconds = self._seconds(stage) if stage else 0.0

        line = self.create({
            "ticket_id": ticket.id, "lane": lane,
            "stage_id": stage.id if stage else False,
            "sequence": last_seq + 10, "timer_type": timer_type,
            "deadline": add_working_seconds(
                fields.Datetime.now(), effective_seconds, window),
        })
        if start_paused:
            line.action_pause("created paused")
        else:
            line._sync_ticket()
        line.ticket_id._log_event("sla_start", to_lane=lane, note=stage.name if stage else "-")
        return line

    # ---------------------------------------------------------------- cron
    RENOTIFY_HOURS = 1.0

    @api.model
    def cron_process(self):
        now = fields.Datetime.now()
        for line in self.search([("state", "=", "running"),
                                 ("deadline", "!=", False),
                                 ("deadline", "<=", now)], limit=100):
            try:
                if line.is_vetting:
                    line._handle_vetting_breach(now)
                else:
                    line._handle_breach(now)
            except Exception:
                _logger.exception("SLA breach failed for ticket %s", line.ticket_id.name)
        return True

    def _handle_vetting_breach(self, now):
        """The lead is late approving a hold: remind the lead, keep the ticket's
        own SLA untouched (that one belongs to the team, not to the lead)."""
        self.ensure_one()
        noise = self.last_notified_at and \
            (now - self.last_notified_at).total_seconds() < self.RENOTIFY_HOURS * 3600
        if noise:
            return
        self.write({"breach_count": self.breach_count + 1, "last_notified_at": now})
        ticket = self.ticket_id
        # the breach must be visible on the ticket, not only in a mailbox
        ticket.sudo().write({"sla_breached": True})
        ticket._log_event("sla_breach", from_lane="lead",
                          note=_("hold approval overdue (#%s)") % self.breach_count)

        team = (ticket.current_team_id or ticket.team_id).sudo()
        if self.breach_count <= 1:
            # first reminder: the lead only
            ticket._send_mail(
                ticket, team.lead_ids.mapped("partner_id"),
                _("Hold approval overdue: %s") % ticket.name,
                ticket._mail_html(
                    _("A hold request on <b>%s</b> has been waiting for you for "
                      "longer than the allowed time. Approve or reject it.") % ticket.name,
                    [(_("Waiting since"), self.started_at),
                     (_("Ticket"), ticket.name),
                     (_("Team clock (unchanged)"), ticket.sla_deadline or _("paused"))],
                    links=[(_("Open ticket"), ticket._mail_url(), "#0d6efd")]))
            return True

        # still nothing? then the lead is the one who is late - follow the
        # escalation step configured for this priority instead of mailing them again
        targets = ticket._vetting_escalation_targets(team)
        if not targets:
            ticket.message_post(body=_(
                "Hold approval is overdue and no escalation step is configured. "
                "Set one in Configuration > Priorities & SLA (Escalate To)."))
            return True
        ticket.sudo().write({"escalation_level": ticket.escalation_level + 1})
        ticket._send_mail(
            ticket, targets.mapped("partner_id"),
            _("Escalated - hold approval overdue: %s") % ticket.name,
            ticket._mail_html(
                _("<b>%s</b> has been waiting for the team lead since <b>%s</b> and "
                  "nobody decided. It is now with you.") % (
                    ticket.name, self.started_at),
                [(_("Ticket"), ticket.name),
                 (_("Location"), ticket.location_id.name),
                 (_("Category"),
                  (ticket.subcategory_id or ticket.category_id).display_name),
                 (_("Held by"), ticket.waiting_on or _("Team Lead")),
                 (_("Team deadline"), ticket.sla_deadline or "-"),
                 (_("Escalation level"), ticket.escalation_level)],
                links=[(_("Open ticket"), ticket._mail_url(), "#d63384")]))
        ticket.message_post(body=_("Hold approval escalated to %s.")
                            % ", ".join(targets.mapped("name")))
        return True

    def _handle_breach(self, now):
        self.ensure_one()
        noise_floor = self.last_notified_at and \
            (now - self.last_notified_at).total_seconds() < self.RENOTIFY_HOURS * 3600
        if noise_floor:
            return
        stage = self.stage_id
        policy = stage.on_breach if stage else "notify_only"
        ticket = self.ticket_id

        # A hold request is waiting for the lead: that lead clock is the one that
        # reminds and escalates, so the team clock must not mail the same person
        # twice. It still records the breach and keeps its own deadline.
        if ticket._vetting_lines():
            self.write({"breach_count": self.breach_count + 1, "last_notified_at": now})
            ticket.sla_breached = True
            ticket._log_event("sla_breach", from_lane=self.lane,
                              note=_("%s clock, breach #%s (waiting for the lead)")
                              % (LANE_LABELS[self.lane], self.breach_count))
            self._sync_ticket()
            return True

        self.write({"breach_count": self.breach_count + 1, "last_notified_at": now})
        ticket.sla_breached = True
        ticket._log_event("sla_breach", from_lane=self.lane,
                          note="%s clock, breach #%s" % (LANE_LABELS[self.lane], self.breach_count))

        if policy != "notify_only" and ticket.escalation_level < stage.level:
            ticket._escalate(stage)
        if policy == "escalate_next":
            nxt = ticket._next_stage(stage)
            if nxt:
                # Category override timing for the next stage level
                override_seconds = None
                cat = ticket.subcategory_id or ticket.category_id
                if cat and ticket.priority_id:
                    timing = cat.get_sla_timing(ticket.priority_id, nxt.level)
                    if timing:
                        override_seconds = float(timing[0]) * {
                            "minutes": 60.0, "hours": 3600.0, "days": 86400.0}.get(timing[1], 3600.0)
                stage_seconds = override_seconds if override_seconds is not None else self._seconds(nxt)
                self.write({"stage_id": nxt.id,
                            "deadline": add_working_seconds(now, stage_seconds, self._window())})
            else:
                self.write({"deadline": add_working_seconds(now, 3600, self._window())})
        # notify_only and escalate_keep both LEAVE THE DEADLINE WHERE IT IS.
        # Pushing it forward made a breached ticket look healthy again ("deadline
        # moved, so nothing is late") and hid the breach from the user. The next
        # sweep is kept quiet by last_notified_at, not by moving the deadline.

        self._sync_ticket()


class SvcTicketSlaTicket(models.Model):
    """Ticket side of the clock: hand-offs, escalation, lane stages."""
    _inherit = "svcticket.ticket"

    sla_line_ids = fields.One2many("svcticket.sla.line", "ticket_id", "SLA Clocks")
    event_ids = fields.One2many("svcticket.event", "ticket_id", "Timeline")

    # ------------------------------------------------------------- events
    def _log_event(self, event_type, from_lane=None, to_lane=None, note=None):
        try:
            self.ensure_one()
            self.env["svcticket.event"].sudo().create({
                "ticket_id": self.id, "event_type": event_type,
                "from_lane": from_lane, "to_lane": to_lane, "note": note,
            })
        except Exception:
            _logger.exception("Timeline entry failed for %s", self.name)

    # -------------------------------------------------------------- lanes
    def _all_open_lines(self):
        return self.sla_line_ids.filtered(lambda l: l.state in ("running", "paused"))

    def _open_lines(self):
        """The team's own clock(s). Vetting clocks are excluded - they run next
        to this one and must never be paused, resumed or restarted with it."""
        return self._all_open_lines().filtered(lambda l: not l.is_vetting)

    def _vetting_lines(self):
        return self._all_open_lines().filtered(lambda l: l.is_vetting)

    def _open_vetting_clock(self, seconds, reason=None):
        """Start the lead's clock next to the team's running clock.

        The team keeps its own deadline: asking for a hold neither buys nor
        burns time. Only the lead's clock is new.
        """
        self.ensure_one()
        self._close_vetting_clock("replaced")
        stage = self._first_stage()
        line = self.env["svcticket.sla.line"].sudo().create({
            "ticket_id": self.id,
            "lane": "lead",
            "is_vetting": True,
            "stage_id": stage.id if stage else False,
            "sequence": max(self.sla_line_ids.mapped("sequence") or [0]) + 10,
            "timer_type": self.priority_id.timer_type if self.priority_id else "business_hours",
            "deadline": add_working_seconds(
                fields.Datetime.now(), seconds or 0.0,
                team_window(self.current_team_id or self.team_id)),
        })
        self._log_event("sla_start", to_lane="lead",
                        note=reason or _("hold approval"))
        return line

    def _close_vetting_clock(self, reason="closed"):
        for line in self._vetting_lines():
            line.action_close(reason)
        return True

    def _first_stage(self):
        """Lowest escalation level of this priority."""
        self.ensure_one()
        return self.env["svcticket.sla.stage"].search([
            ("priority_id", "=", self.priority_id.id), ("active", "=", True),
        ], order="level", limit=1)

    def _next_stage(self, current):
        """The level above `current`, if any."""
        self.ensure_one()
        if not current:
            return self.env["svcticket.sla.stage"]
        return self.env["svcticket.sla.stage"].search([
            ("priority_id", "=", self.priority_id.id),
            ("active", "=", True), ("level", ">", current.level),
        ], order="level", limit=1)

    def _team_for_lane(self, lane):
        """Fallback owner for a lane, used only when no routing rule set one."""
        self.ensure_one()
        mapping = {"facility": "facility", "po": "po_team", "payment": "account_team"}
        team_type = mapping.get(lane)
        if team_type:
            team = self.env["svcticket.team"].search(
                [("team_type", "=", team_type), ("active", "=", True)], limit=1)
            if team:
                return team
        return self.team_id

    def _pause_clock(self, reason="paused"):
        """Freeze the running clock. Time already spent is kept."""
        self.ensure_one()
        self._open_lines().action_pause(reason)
        return True

    def _resume_clock(self, reason="resumed"):
        """Start a paused clock again where it left off."""
        self.ensure_one()
        self._open_lines().action_resume(reason)
        return True

    def _open_clock(self, lane, start_paused=False, note=None, seconds=None):
        """Close the running clock and start a new one. Ownership is NOT touched
        here - the routing rules decide who holds the ticket."""
        self.ensure_one()
        self._close_vetting_clock("lane changed")
        for line in self._open_lines():
            line.action_close("handoff")
        line = self.env["svcticket.sla.line"].open_for(
            self, lane, start_paused=start_paused, seconds=seconds)
        if note:
            self._log_event("handoff", to_lane=lane, note=note)
        return line

    def _set_state(self, state, note=None):
        old = self.state
        self.write({"state": state})
        self._log_event("state", note=note or "%s -> %s" % (old, state))
        return old

    def _vetting_escalation_targets(self, team=None):
        """Who takes over when the team lead is the one who lets a hold rot.

        It follows what the priority is configured with: the level's "Escalate
        To" people if any were picked. When nothing is configured at all it falls
        back to the Service Ticket managers, so a forgotten approval can never
        sit silently forever.
        """
        self.ensure_one()
        team = team or (self.current_team_id or self.team_id).sudo()
        stage = self._next_stage(self.sla_stage_id) or self.sla_stage_id \
            or self._first_stage()
        targets = self.env["res.users"]
        if stage and stage.escalate_to == "approvers" and stage.approver_ids:
            targets = stage.approver_ids.mapped("user_id")
        if not targets:
            targets = self.env["res.users"].sudo().search(
                [("groups_id", "in", self.env.ref("service_ticket.group_manager").id),
                 ("active", "=", True)])
        return targets - team.lead_ids

    # ---------------------------------------------------------- escalation
    def _escalate(self, stage=None):
        """Notify whoever this SLA level says, and bump the level counter."""
        self.ensure_one()
        stage = stage or self.sla_stage_id
        if not stage:
            return
        self.escalation_level += 1
        targets = stage._target_users(self) - self.env.user
        if not targets:
            self.message_post(body=_("Level %s breached. No escalation target configured.")
                              % stage.level)
            return
        codes = stage.notify_channel_ids.mapped("code") or None
        self.env["svcticket.channel"].notify(
            self, targets.mapped("partner_id"),
            _("Escalation: %s") % self.name,
            _("<b>%s</b> breached SLA level %s (%s). Please take over.")
            % (self.name, stage.level, stage.name), codes=codes)
        # this method runs ON the ticket (self IS the ticket): there is no
        # self.ticket_id here. That leftover line raised AttributeError, the cron
        # swallowed it, and no escalation mail was ever sent.
        ticket = self
        ticket._send_mail(
            ticket, targets.mapped("partner_id"),
            _("SLA breached: %s") % ticket.name,
            ticket._mail_html(
                _("<b>%s</b> went past its deadline and is now at escalation level "
                  "%s (%s).") % (ticket.name, stage.level, stage.name),
                [(_("Location"), ticket.location_id.name),
                 (_("Category"),
                  (ticket.subcategory_id or ticket.category_id).display_name),
                 (_("Deadline was"), ticket.sla_deadline),
                 (_("With"), ticket.waiting_on),
                 (_("Escalated to"), ", ".join(targets.mapped("name")))]))
        self.message_post(body=_("Escalated to %s.") % ", ".join(targets.mapped("name")))
