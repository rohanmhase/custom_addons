# -*- coding: utf-8 -*-
"""All small configuration models in one place.

Location, Category, Priority, SLA Stage, Team, Escalation, Approver, Hold Reason.
"""
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class SvcTicketLocation(models.Model):
    _name = "svcticket.location"
    _description = "Service Location"
    _order = "location_type, name"

    name = fields.Char(required=True, index=True)
    location_type = fields.Selection([
        ("clinic", "Clinic"), ("warehouse", "Warehouse"),
        ("office", "Office"), ("other", "Other"),
    ], default="clinic", required=True, index=True)
    source_model = fields.Char(readonly=True, index=True)
    source_id = fields.Integer(readonly=True, index=True)
    address = fields.Text()
    contact_person_ids = fields.Many2many(
        "res.users", "svcticket_location_user_rel", "location_id", "user_id",
        string="Clinic Contacts",
        help="These users can see and follow every ticket raised at this location.")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("source_uniq", "unique(source_model, source_id)",
         "This source record is already linked to a location."),
    ]

    @api.model
    def _sync_from(self, model, location_type):
        if model not in self.env:
            return 0
        Location = self.sudo()
        records = self.env[model].sudo().search([])
        existing = {
            (loc.source_model, loc.source_id): loc
            for loc in Location.search([("source_model", "=", model)])
        }
        created = 0
        for rec in records:
            loc = existing.get((model, rec.id))
            if loc:
                if loc.name != rec.display_name:
                    loc.name = rec.display_name
                continue
            Location.create({
                "name": rec.display_name,
                "location_type": location_type,
                "source_model": model,
                "source_id": rec.id,
                "active": getattr(rec, "active", True),
            })
            created += 1
        return created

    @api.model
    def sync_all(self):
        total = 0
        for model, kind in (("clinic.clinic", "clinic"),
                            ("stock.warehouse", "warehouse"),
                            ("res.company", "office")):
            total += self._sync_from(model, kind)
        _logger.info("Service Ticket: %s locations synced", total)
        return total

    def action_sync_now(self):
        count = self.sync_all()
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success", "next": {"type": "ir.actions.act_window_close"},
                       "message": _("%s location(s) synced.") % count},
        }


class SvcTicketCategory(models.Model):
    _name = "svcticket.category"
    _description = "Service Ticket Category"
    _order = "sequence, name"
    _parent_name = "parent_id"
    _parent_store = True

    name = fields.Char(required=True)
    code = fields.Char()
    parent_id = fields.Many2one("svcticket.category", "Parent", ondelete="cascade", index=True)
    parent_path = fields.Char(index=True, unaccent=False)
    child_ids = fields.One2many("svcticket.category", "parent_id", "Sub-Categories")
    default_priority_id = fields.Many2one("svcticket.priority", "Default Priority")
    allow_other = fields.Boolean("Allow 'Other'", default=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    requires_approval = fields.Boolean(
        "Genuine Check Required",
        help="Tickets in this category only start work after an approver accepts them.")
    approver_ids = fields.Many2many(
        "svcticket.approver", "svcticket_category_approver_rel",
        "category_id", "approver_id", "Approvers")

    require_intake_photo = fields.Boolean("Photos Required at Creation")
    min_intake_photos = fields.Integer("Min Photos", default=1)
    require_resolution_photo = fields.Boolean("Photos Required at Resolution", default=True)
    min_resolution_photos = fields.Integer("Min Photos", default=1)

    @api.constrains("parent_id")
    def _check_parent_recursion(self):
        if not self._check_recursion():
            raise models.ValidationError(_("Categories cannot be recursive."))

    def name_get(self):
        return [(c.id, "%s / %s" % (c.parent_id.name, c.name) if c.parent_id else c.name)
                for c in self]


class SvcTicketPriority(models.Model):
    _name = "svcticket.priority"
    _description = "Service Ticket Priority"
    _order = "sequence, id"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    color = fields.Integer(default=0)
    sequence = fields.Integer(default=10)
    timer_type = fields.Selection([
        ("24_7", "24/7 Countdown"),
        ("business_hours", "Business Hours (team schedule)"),
    ], default="business_hours", required=True)
    stage_ids = fields.One2many("svcticket.sla.stage", "priority_id", "SLA Stages")
    active = fields.Boolean(default=True)

    _sql_constraints = [("code_uniq", "unique(code)", "Priority code must be unique.")]

    # ---------------------------------------------------------- default SLA
    DEFAULT_STAGES = {
        "P1": [("Acknowledge", 1, "hours", "escalate_next"),
               ("Fix", 4, "hours", "escalate_next"),
               ("Resolve", 8, "hours", "escalate_next")],
        "P2": [("Acknowledge", 2, "hours", "escalate_next"),
               ("Fix", 24, "hours", "escalate_next"),
               ("Resolve", 48, "hours", "escalate_next")],
        "P3": [("Acknowledge", 4, "hours", "escalate_next"),
               ("Fix", 72, "hours", "escalate_next"),
               ("Resolve", 120, "hours", "escalate_next")],
        "P4": [("Acknowledge", 8, "hours", "notify_only"),
               ("Fix", 7, "days", "notify_only"),
               ("Resolve", 14, "days", "escalate_next")],
        "UNK": [("Acknowledge", 4, "hours", "escalate_next"),
                ("Fix", 72, "hours", "escalate_next"),
                ("Resolve", 7, "days", "escalate_next")],
    }

    @api.model
    def _seed_stages(self):
        """Create the default SLA levels once, and never fight an existing row.

        Written in Python on purpose: the XML loader creates (it cannot update
        by natural key), so a stage row that lost its external id used to make
        the upgrade fail on the unique(priority_id, level) constraint.
        """
        Stage = self.env["svcticket.sla.stage"].with_context(active_test=False)
        for priority in self.search([]):
            plan = self.DEFAULT_STAGES.get(priority.code or "", [])
            for index, (name, limit, unit, breach) in enumerate(plan, start=1):
                vals = {
                    "priority_id": priority.id, "level": index, "name": name,
                    "time_limit": limit, "time_unit": unit, "on_breach": breach,
                    "escalate_to": "team_lead",
                }
                existing = Stage.search([
                    ("priority_id", "=", priority.id), ("level", "=", index)], limit=1)
                if existing:
                    existing.write(vals)
                else:
                    Stage.create(vals)
        return True


class SvcTicketSlaStage(models.Model):
    """One escalation level of a priority's SLA.

    Nothing here is tied to a team: level 1 breaches to the ticket's own team
    lead, deeper levels to whoever you pick from the approver registry.
    """
    _name = "svcticket.sla.stage"
    _description = "SLA Escalation Level"
    _order = "priority_id, level, id"

    priority_id = fields.Many2one("svcticket.priority", required=True, ondelete="cascade")
    level = fields.Integer("Level", default=1, required=True)
    name = fields.Char(required=True, help="Acknowledge, Fix, Resolve...")
    time_limit = fields.Float(required=True, default=1.0)
    time_unit = fields.Selection([
        ("minutes", "Minutes"), ("hours", "Hours"), ("days", "Days"),
    ], default="hours", required=True)
    on_breach = fields.Selection([
        ("escalate_next", "Escalate to next level"),
        ("escalate_keep", "Escalate and keep ticking"),
        ("notify_only", "Notify only"),
    ], default="escalate_next", required=True)
    escalate_to = fields.Selection([
        ("team_lead", "Team Lead of the assigned team"),
        ("approvers", "Specific people (approver registry)"),
    ], default="team_lead", required=True)
    approver_ids = fields.Many2many(
        "svcticket.approver", "svcticket_stage_approver_rel",
        "stage_id", "approver_id", "Escalate To People")
    notify_channel_ids = fields.Many2many(
        "svcticket.channel", "svcticket_stage_channel_rel",
        "stage_id", "channel_id", "Notify Via")
    active = fields.Boolean(default=True)

    def _seconds(self):
        return float(self.time_limit or 0) * {
            "minutes": 60.0, "hours": 3600.0, "days": 86400.0}.get(self.time_unit, 3600.0)

    def _target_users(self, ticket):
        """Who gets pinged when this level breaches.

        The first breach always goes to the team lead. Once the lead is the one
        who is late, the deeper levels follow whatever is configured for that
        priority (`escalate_to` + the approvers picked on the level).
        """
        self.ensure_one()
        lead = (ticket.current_team_id or ticket.team_id).sudo().lead_ids
        if self.level <= 1 or self.escalate_to == "team_lead" or not self.approver_ids:
            return lead
        return self.approver_ids.mapped("user_id") | lead

    _sql_constraints = [("level_uniq", "unique(priority_id, level)",
                         "Level must be unique per priority.")]

    def _auto_init(self):
        """Clear duplicate levels BEFORE Odoo tries to add the unique
        constraint, otherwise the constraint is silently skipped forever."""
        self.env.cr.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'svcticket_sla_stage'")
        if self.env.cr.fetchone():
            with self.env.cr.savepoint():
                self.env.cr.execute("""
                    DELETE FROM svcticket_sla_stage a
                    USING svcticket_sla_stage b
                    WHERE a.priority_id = b.priority_id
                      AND a.level = b.level
                      AND a.id > b.id
                """)
        return super()._auto_init()

    @api.model
    def _dedupe(self):
        """Old upgrades left duplicate level rows behind. Keep one per
        (priority, level) so the unique constraint can be applied again."""
        self.env.cr.execute("""
            DELETE FROM svcticket_sla_stage a
            USING svcticket_sla_stage b
            WHERE a.priority_id = b.priority_id
              AND a.level = b.level
              AND a.id > b.id
        """)
        if self.env.cr.rowcount:
            _logger.warning("Removed %s duplicate SLA stage rows", self.env.cr.rowcount)
        return True


class SvcTicketTeam(models.Model):
    _name = "svcticket.team"
    _description = "Service Ticket Team"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    category_ids = fields.Many2many("svcticket.category", "svcticket_team_category_rel",
                                    "team_id", "category_id", "Major Categories")
    subcategory_ids = fields.Many2many("svcticket.category", "svcticket_team_subcategory_rel",
                                       "team_id", "category_id", "Sub-Categories",
                                       domain="[('parent_id','!=',False)]")
    location_ids = fields.Many2many("svcticket.location", "svcticket_team_location_rel",
                                    "team_id", "location_id", "Locations",
                                    help="Empty = all locations.")
    member_ids = fields.Many2many("res.users", "svcticket_team_member_rel", "team_id", "user_id", "Members")
    lead_ids = fields.Many2many("res.users", "svcticket_team_lead_rel", "team_id", "user_id", "Team Leads")

    team_type = fields.Selection([
        ("facility", "Facility Team"),
        ("po_team", "PO Team"),
        ("account_team", "Accounts Team"),
        ("other", "Other"),
    ], default="facility", required=True, index=True,
        help="Used by the routing rules as a target. Set exactly one team as each type.")

    assignment_mode = fields.Selection([
        ("broadcast", "Broadcast to members"), ("manual", "Manual assignment"),
    ], default="broadcast", required=True)
    broadcast_timeout = fields.Float("Broadcast Timeout (hours)", default=4.0,
                                     help="If nobody accepts in time, escalate to team leads.")

    work_start_hour = fields.Float("Start", default=9.0)
    work_end_hour = fields.Float("End", default=19.0)
    work_mon = fields.Boolean("Mon", default=True)
    work_tue = fields.Boolean("Tue", default=True)
    work_wed = fields.Boolean("Wed", default=True)
    work_thu = fields.Boolean("Thu", default=True)
    work_fri = fields.Boolean("Fri", default=True)
    work_sat = fields.Boolean("Sat", default=True)
    work_sun = fields.Boolean("Sun", default=False)

    active = fields.Boolean(default=True)
    escalation_rule_ids = fields.One2many("svcticket.team.escalation", "team_id",
                                          "Escalation Levels",
                                          help="Per-team escalation persons per level.")

class SvcTicketTeamEscalation(models.Model):
    _name = "svcticket.team.escalation"
    _description = "Team Escalation Level"
    _order = "team_id, level"

    team_id = fields.Many2one("svcticket.team", required=True, ondelete="cascade")
    level = fields.Integer("Escalation Level", required=True, default=1,
        help="Level 0 = team member, 1 = team lead, 2+ = escalation persons.")
    user_ids = fields.Many2many("res.users", "svcticket_team_escalation_user_rel",
                                "escalation_id", "user_id", "Notify These People",
        help="People to mail when the ticket reaches this escalation level.")
    active = fields.Boolean(default=True)

    _sql_constraints = [("level_uniq", "unique(team_id, level)",
                         "Level must be unique per team.")]


class SvcTicketApprover(models.Model):
    _name = "svcticket.approver"
    _description = "Approver Registry"
    _order = "sequence, id"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    partner_id = fields.Many2one("res.partner", related="user_id.partner_id", store=True)
    job_title = fields.Char(help="Director, Owner, CFO...")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    @api.depends("user_id", "job_title")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = ("%s (%s)" % (rec.user_id.name, rec.job_title)
                                if rec.job_title else rec.user_id.name or "-")

    _sql_constraints = [("user_uniq", "unique(user_id)", "This user is already an approver.")]


class SvcTicketHoldReason(models.Model):
    _name = "svcticket.hold.reason"
    _description = "Hold Reason"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    needs_financials = fields.Boolean("Requires PO / Cost", help="Sends the ticket to the PO team.")
    active = fields.Boolean(default=True)
