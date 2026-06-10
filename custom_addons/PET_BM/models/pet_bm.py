from odoo import models, fields, api
from odoo.exceptions import ValidationError
from datetime import date, timedelta


class PETPatientStatus(models.Model):
    _name = 'pet.patient.status'
    _description = 'Patient Treatment Status'

    name = fields.Char(string="Status Name", required=True)
    active = fields.Boolean(default=True)

    def init(self):
        # FIXED: Ensure only actual Treatment Statuses are generated
        default_statuses = [
            'Continue Treatment', 'Planning to Stop',
            'Stopped', 'Not Started', 'Completed', 'Maintenance'
        ]
        for status in default_statuses:
            if not self.search([('name', '=', status)]):
                self.create({'name': status})


class PETCategory(models.Model):
    _name = 'pet.category'
    _description = 'PET Category'
    name = fields.Char(string="Category Name", required=True)

    def init(self):
        # 1. Forcefully rename the incorrect database typo to the proper name
        incorrect_categories = self.search([('name', 'ilike', 'not enrolled')])
        for cat in incorrect_categories:
            cat.write({'name': 'Not Enrolled'})

        # 2. Guarantee the 4 exact master categories exist safely in the database
        master_categories = ['Active', 'Drop-off', 'Not Enrolled', 'Completed']
        for cat_name in master_categories:
            if not self.search([('name', '=', cat_name)]):
                self.create({'name': cat_name})


class PETSubCategory(models.Model):
    _name = 'pet.subcategory'
    _description = 'PET Sub Category'
    category_id = fields.Many2one('pet.category', string="Category", required=True, ondelete='cascade')
    name = fields.Char(string="Sub Category Name", required=True)


# =========================================================================
# THE NEW DEDICATED SLA TICKETING SYSTEM
# =========================================================================
class PetEscalationTicket(models.Model):
    _name = 'pet.escalation.ticket'
    _description = 'PET Escalation Ticket'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'
    _rec_name = 'ticket_sequence'

    ticket_sequence = fields.Char(string="Ticket ID", required=True, copy=False, readonly=True, default="New")
    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, readonly=True)
    pet_record_id = fields.Many2one('pet.record', string="PET Dashboard", required=True, readonly=True)
    bm_log_id = fields.Many2one('bm.followup.log', string="BM Log", readonly=True)

    pet_agent_id = fields.Many2one('res.users', string="Raised By (PET)", readonly=True)
    assigned_bm_id = fields.Many2one('res.users', string="Assigned BM", readonly=True)

    status = fields.Selection([
        ('pending', 'Pending BM Action'),
        ('overdue', 'Overdue (SLA Breached)'),
        ('resolved', 'Resolved')
    ], string="Ticket Status", default='pending', tracking=True)

    issue_description = fields.Text(string="Issue Description", required=True, readonly=True)
    resolution_remarks = fields.Text(string="BM Resolution Remarks", tracking=True)

    deadline = fields.Datetime(string="SLA Deadline (24 Hrs)", readonly=True, tracking=True)
    reopen_count = fields.Integer(string="Times Reopened", default=0, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('ticket_sequence', 'New') == 'New':
                vals['ticket_sequence'] = self.env['ir.sequence'].next_by_code('pet.escalation.ticket') or 'TKT-NEW'

            vals['deadline'] = fields.Datetime.now() + timedelta(hours=24)

        return super().create(vals_list)

    def action_resolve(self):
        """ BM resolves the ticket """
        for rec in self:
            if not rec.resolution_remarks:
                raise ValidationError("You must provide Resolution Remarks before closing this ticket.")

            rec.status = 'resolved'
            rec.message_post(body=f"Ticket marked as RESOLVED by {self.env.user.name}.")

            # Clear BM's To-Do list
            activities = self.env['mail.activity'].search(
                [('res_model', '=', 'pet.escalation.ticket'), ('res_id', '=', rec.id)])
            activities.action_done()

            rec.message_post(
                body=f"BM Resolution: {rec.resolution_remarks}",
                partner_ids=[rec.pet_agent_id.partner_id.id]
            )

    def action_reopen(self):
        """ PET Team rejects resolution and bounces it back to BM """
        for rec in self:
            rec.status = 'pending'
            rec.reopen_count += 1
            rec.deadline = fields.Datetime.now() + timedelta(hours=24)
            rec.resolution_remarks = False

            rec.message_post(body=f"Ticket REOPENED by {self.env.user.name}. SLA reset to 24 Hours.")

            rec.activity_schedule(
                activity_type_id=self.env.ref('mail.mail_activity_data_todo').id,
                user_id=rec.assigned_bm_id.id,
                note='<strong>Ticket Reopened:</strong> Patient still has an issue.',
                summary='Ticket Reopened'
            )

    @api.model
    def _cron_check_sla(self):
        now = fields.Datetime.now()
        overdue_tickets = self.search([('status', '=', 'pending'), ('deadline', '<', now)])
        for ticket in overdue_tickets:
            ticket.status = 'overdue'
            ticket.message_post(body="🚨 SLA Breached. Status changed to Overdue.")

    @api.model
    def _cron_end_of_day_summary(self):
        pending_count = self.search_count([('status', 'in', ['pending', 'overdue'])])
        if pending_count > 0:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.info(f"END OF DAY TICKET REPORT: There are {pending_count} unresolved escalations.")


# =========================================================================
# THE DIARY SNAPSHOT WIZARD
# =========================================================================
class PETFollowupLine(models.Model):
    _name = 'pet.followup.line'
    _description = 'PET Follow-up Snapshot'
    _order = 'contact_date desc, id desc'

    pet_record_id = fields.Many2one('pet.record', string="PET Master Record", required=True, ondelete='cascade')
    is_locked = fields.Boolean(compute='_compute_is_locked')

    contact_date = fields.Date(string="Date of Contact", default=fields.Date.context_today, required=True)
    user_id = fields.Many2one('res.users', string="Advisor", default=lambda self: self.env.user, readonly=True)

    not_connected = fields.Boolean(string="Not Connected (No Answer)")

    start_date = fields.Date(related='pet_record_id.start_date', string="Start Date")
    last_visit_date = fields.Date(related='pet_record_id.last_visit_date', string="Last Visit Date")

    actual_next_followup_date = fields.Date(string="Actual Next Follow-up Date", required=True,
                                            default=fields.Date.context_today)

    suggested_followup_days = fields.Integer(string="Suggested Follow-up Days", compute="_compute_line_followup_logic",
                                             store=True)
    recommended_next_followup = fields.Date(string="Recommended Next Follow-up", compute="_compute_line_followup_logic",
                                            store=True)
    followup_overdue = fields.Integer(string="Follow-up Overdue (Days)", compute="_compute_line_metrics", store=True)
    priority = fields.Selection([('0', 'Low'), ('1', 'Medium'), ('2', 'High')], string="Priority",
                                compute="_compute_line_metrics", store=True)
    escalation_needed = fields.Selection([('no', 'No'), ('yes', 'Yes')], string="Escalation Needed",
                                         compute="_compute_line_metrics", store=True)
    task_status = fields.Selection(
        [('no_set', 'No follow-up set'), ('overdue', 'Overdue Follow-up'), ('today', 'Due Today'),
         ('on_track', 'On Track')], string="Task Status", compute="_compute_line_metrics", store=True)

    category_id = fields.Many2one('pet.category', string="Category")
    subcategory_id = fields.Many2one('pet.subcategory', string="Sub Category")
    patient_status = fields.Many2one('pet.patient.status', string="Treatment Status")
    outcome_status = fields.Selection(
        [('On Track', 'On Track'), ('Improving', 'Improving'), ('Plateau', 'Plateau'), ('Stopped', 'Stopped'),
         ('completed', 'Completed')], string="Outcome Status")
    reason_not_starting_stopping = fields.Text(string="Reason for Not Starting / Stopping")

    pain_walking_score = fields.Integer(string="Pain Walking (0-10)")
    pain_resting_score = fields.Integer(string="Pain Resting (0-10)")
    satisfaction_score = fields.Integer(string="Satisfaction (0-10)")
    mobility_status = fields.Selection(
        [('Severe Limitation', 'Severe'), ('Moderate Limitation', 'Moderate'), ('Mild Limitation', 'Mild'),
         ('Independent', 'Independent')], string="Mobility Status")
    therapy_kit_status = fields.Selection([('yes', 'Yes'), ('no', 'No'), ('maybe', 'Maybe')], string="Home Therapy Kit")

    discount_offered = fields.Float(string="Discount Offered (%)")

    escalate_to_bm = fields.Boolean(string="Escalate Issue to BM")
    escalation_description = fields.Text(string="Issue Description")

    referral_given = fields.Boolean(string="Referral Given")
    review_given = fields.Boolean(string="Review Given")
    referral_name = fields.Char(string="Referral Name")
    referral_contact = fields.Char(string="Referral Number")
    review_link = fields.Char(string="Review Link/Details")

    action_taken = fields.Text(string="Action Taken")
    remarks = fields.Text(string="Remarks")

    def _compute_is_locked(self):
        for rec in self:
            rec.is_locked = bool(rec.id)

    @api.constrains('pain_walking_score', 'pain_resting_score', 'satisfaction_score')
    def _check_scores(self):
        for rec in self:
            if not rec.not_connected:
                if rec.pain_walking_score < 0 or rec.pain_walking_score > 10: raise ValidationError(
                    "Pain score must be between 0 and 10.")
                if rec.pain_resting_score < 0 or rec.pain_resting_score > 10: raise ValidationError(
                    "Pain score must be between 0 and 10.")
                if rec.satisfaction_score < 0 or rec.satisfaction_score > 10: raise ValidationError(
                    "Satisfaction score must be between 0 and 10.")

    @api.constrains('discount_offered')
    def _check_discount_percentage(self):
        for rec in self:
            if rec.discount_offered < 0 or rec.discount_offered > 100:
                raise ValidationError("Discount percentage must be between 0 and 100.")

    @api.depends('contact_date', 'actual_next_followup_date', 'subcategory_id', 'category_id', 'pain_walking_score',
                 'pain_resting_score', 'satisfaction_score', 'remarks')
    def _compute_line_metrics(self):
        today = fields.Date.context_today(self)
        for rec in self:
            overdue = max(0, (today - rec.actual_next_followup_date).days) if rec.actual_next_followup_date else 0
            rec.followup_overdue = overdue

            sub_name = (rec.subcategory_id.name or "").strip().lower()
            cat_name = (rec.category_id.name or "").strip().lower()
            max_pain = max(rec.pain_walking_score or 0, rec.pain_resting_score or 0)

            if sub_name == "drop-risk" or overdue >= 3 or max_pain >= 8:
                rec.priority = '2'
            elif sub_name == "irregular" or overdue >= 1 or max_pain >= 6 or cat_name == "drop-off":
                rec.priority = '1'
            else:
                rec.priority = '0'

            rem = (rec.remarks or "").strip().lower()
            if (rec.priority == '2' or (
                    cat_name == 'completed' and (rec.satisfaction_score or 0) < 7) or rem == 'lost trust'):
                rec.escalation_needed = 'yes'
            else:
                rec.escalation_needed = 'no'

            if not rec.actual_next_followup_date:
                rec.task_status = 'no_set'
            elif overdue > 0:
                rec.task_status = 'overdue'
            elif rec.actual_next_followup_date == today:
                rec.task_status = 'today'
            else:
                rec.task_status = 'on_track'

    @api.depends('category_id', 'subcategory_id', 'contact_date')
    def _compute_line_followup_logic(self):
        for rec in self:
            days = 0
            cat = (rec.category_id.name or "").strip().lower()
            sub_cat = (rec.subcategory_id.name or "").strip().lower()

            if cat == 'active':
                days = 2 if sub_cat == 'irregular' else 7
            elif cat == 'drop-off':
                days = 1 if sub_cat == 'drop-risk' else 3
            # AIRTIGHT FALLBACK: Catches both variations
            elif cat in ['not enrolled', 'not started']:
                days = 1 if sub_cat == 'hot' else (2 if sub_cat == 'warm' else (7 if sub_cat == 'cold' else 3))
            elif cat == 'completed':
                days = 7 if sub_cat == 'unsatisfied' else 30
            else:
                days = 0

            rec.suggested_followup_days = days
            rec.recommended_next_followup = rec.contact_date + timedelta(
                days=days) if rec.contact_date and days > 0 else False

    def action_save_log(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window_close'}

    @api.onchange('category_id')
    def _onchange_category_id(self):
        """Clears subcategory and status when category changes"""
        self.subcategory_id = False
        self.patient_status = False

    @api.onchange('subcategory_id')
    def _onchange_subcategory_id(self):
        """Clears status when subcategory changes to prevent mismatches"""
        self.patient_status = False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            if rec.pet_record_id:
                update_vals = {
                    'last_contact_date': rec.contact_date,
                    'actual_next_followup_date': rec.actual_next_followup_date,
                    'action_taken': rec.action_taken,
                    'remarks': rec.remarks,
                }

                if not rec.not_connected:
                    update_vals.update({
                        'category_id': rec.category_id.id if rec.category_id else False,
                        'subcategory_id': rec.subcategory_id.id if rec.subcategory_id else False,
                        'patient_status': rec.patient_status.id if rec.patient_status else False,
                        'outcome_status': rec.outcome_status,
                        'reason_not_starting_stopping': rec.reason_not_starting_stopping,
                        'pain_walking_score': rec.pain_walking_score,
                        'pain_resting_score': rec.pain_resting_score,
                        'satisfaction_score': rec.satisfaction_score,
                        'mobility_status': rec.mobility_status,
                        'therapy_kit_status': rec.therapy_kit_status,
                        'discount_offered': rec.discount_offered,
                        'referral_given': rec.referral_given,
                        'review_given': rec.review_given,
                        'referral_name': rec.referral_name,
                        'referral_contact': rec.referral_contact,
                        'review_link': rec.review_link,
                    })
                rec.pet_record_id.write(update_vals)

                if not rec.not_connected:
                    latest_bm_log = self.env['bm.followup.log'].search(
                        [('patient_id', '=', rec.pet_record_id.patient_id.id)], order='timestamp desc', limit=1)

                    if latest_bm_log:
                        # 1. SYNC REVENUE INSTANTLY
                        if rec.discount_offered > 0:
                            latest_bm_log.write({
                                'pet_discount_offered': rec.discount_offered,
                                'pet_agent_id': rec.user_id.id
                            })

                        # 2. TICKETING ENGINE
                        if rec.escalate_to_bm and rec.escalation_description:
                            assigned_bm = latest_bm_log.user_id

                            new_ticket = self.env['pet.escalation.ticket'].create({
                                'patient_id': rec.pet_record_id.patient_id.id,
                                'pet_record_id': rec.pet_record_id.id,
                                'bm_log_id': latest_bm_log.id,
                                'pet_agent_id': rec.user_id.id,
                                'assigned_bm_id': assigned_bm.id if assigned_bm else self.env.user.id,
                                'issue_description': rec.escalation_description,
                            })

                            if assigned_bm:
                                new_ticket.message_subscribe(partner_ids=[assigned_bm.partner_id.id])

                            new_ticket.activity_schedule(
                                'mail.mail_activity_data_todo',
                                user_id=new_ticket.assigned_bm_id.id,
                                note=f'<strong>Patient Escalation:</strong> {rec.escalation_description}',
                                summary=f'SLA Ticket Created: {new_ticket.ticket_sequence}'
                            )

                            new_ticket.message_post(
                                body=f"<h3>New Escalation Ticket Assigned</h3>"
                                     f"<p>A new ticket has been assigned to you regarding patient <b>{new_ticket.patient_id.name}</b>.</p>"
                                     f"<p><b>Issue:</b> {rec.escalation_description}</p>"
                                     f"<p>Please click here to view the ticket: "
                                     f"<a href='/web#id={new_ticket.id}&model=pet.escalation.ticket&view_type=form'>Open Ticket</a></p>",
                                message_type="notification",
                                subtype_xmlid="mail.mt_note",
                                partner_ids=[assigned_bm.partner_id.id] if assigned_bm else []
                            )
        return records


# =========================================================================
# THE MASTER RECORD
# =========================================================================
class PETRecord(models.Model):
    _name = 'pet.record'
    _description = 'Patient Experience Tracker'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, ondelete='cascade', tracking=True)
    patient_mrn = fields.Char(related="patient_id.mrn", string="MRN", readonly=True)
    clinic_id = fields.Many2one('clinic.clinic', string="Clinic Name", related="patient_id.clinic_id", store=True)
    advisor_id = fields.Many2one('res.users', string="Advisor Name", default=lambda self: self.env.user, tracking=True)
    phone_number = fields.Char(related="patient_id.phone", string="Phone Number", readonly=True)
    alternate_number = fields.Char(string='Alternate Phone Number', tracking=True)

    latest_bm_offer_price = fields.Float(string="Latest Quoted Price", compute="_compute_bm_offer")
    latest_bm_therapies = fields.Integer(string="Quoted Therapies", compute="_compute_bm_offer")

    start_date = fields.Date(string="Start Date", tracking=True)
    last_visit_date = fields.Date(string="Last Visit Date", tracking=True)
    suggested_followup_days = fields.Integer(string="Suggested Follow-up Days", compute="_compute_followup_logic",
                                             store=True, readonly=True)
    recommended_next_followup = fields.Date(string="Recommended Next Follow-up", compute="_compute_followup_logic",
                                            store=True, readonly=True)

    followup_line_ids = fields.One2many('pet.followup.line', 'pet_record_id', string="Follow-up History")
    ticket_ids = fields.One2many('pet.escalation.ticket', 'pet_record_id', string="Escalation Tickets")

    last_contact_date = fields.Date(string="Last Contact Date", tracking=True)
    actual_next_followup_date = fields.Date(string="Actual Next Follow-up Date", tracking=True)

    category_id = fields.Many2one('pet.category', string="Category", tracking=True)
    subcategory_id = fields.Many2one('pet.subcategory', string="Sub Category",
                                     domain="[('category_id', '=', category_id)]", tracking=True)
    patient_status = fields.Many2one('pet.patient.status', string="Current Treatment Status", tracking=True)
    outcome_status = fields.Selection(
        [('On Track', 'On Track'), ('Improving', 'Improving'), ('Plateau', 'Plateau'), ('Stopped', 'Stopped'),
         ('completed', 'Completed')], string="Outcome Status", tracking=True)
    reason_not_starting_stopping = fields.Text(string="Reason for Not Starting / Stopping", tracking=True)

    pain_walking_score = fields.Integer(string="Pain While Walking (0-10)", tracking=True)
    pain_resting_score = fields.Integer(string="Pain Resting (0-10)", tracking=True)
    satisfaction_score = fields.Integer(string="Satisfaction Score (0-10)", tracking=True)
    mobility_status = fields.Selection(
        [('Severe Limitation', 'Severe'), ('Moderate Limitation', 'Moderate'), ('Mild Limitation', 'Mild'),
         ('Independent', 'Independent')], string="Mobility Status", tracking=True)
    therapy_kit_status = fields.Selection([('yes', 'Yes'), ('no', 'No'), ('maybe', 'Maybe')], string="Home Therapy Kit",
                                          tracking=True)

    discount_offered = fields.Float(string="Discount Offered (%)", tracking=True)

    referral_given = fields.Boolean(string="Referral Given", tracking=True)
    review_given = fields.Boolean(string="Review Given", tracking=True)
    referral_name = fields.Char(string="Referral Name", tracking=True)
    referral_contact = fields.Char(string="Referral Number", tracking=True)
    review_link = fields.Char(string="Review Link/Details", tracking=True)

    action_taken = fields.Text(string="Action Taken", tracking=True)
    remarks = fields.Text(string="Remarks", tracking=True)

    allowed_status_ids = fields.Many2many('pet.patient.status', compute='_compute_allowed_statuses', store=False)
    days_since_last_contact = fields.Integer(string="Days Since Last Contact", compute="_compute_all_metrics",
                                             store=True, readonly=True, compute_sudo=True)
    followup_overdue = fields.Integer(string="Follow-up Overdue (Days)", compute="_compute_all_metrics", store=True,
                                      readonly=True, compute_sudo=True)
    priority = fields.Selection([('0', 'Low'), ('1', 'Medium'), ('2', 'High')], string="Priority",
                                compute="_compute_all_metrics", store=True, readonly=True, compute_sudo=True)
    escalation_needed = fields.Selection([('no', 'No'), ('yes', 'Yes')], string="Escalation Needed",
                                         compute="_compute_all_metrics", store=True, readonly=True, compute_sudo=True)
    task_status = fields.Selection(
        [('no_set', 'No follow-up set'), ('overdue', 'Overdue Follow-up'), ('today', 'Due Today'),
         ('on_track', 'On Track')], string="Task Status", compute="_compute_all_metrics", store=True, readonly=True,
        compute_sudo=True)

    def init(self):
        self.env.cr.execute(
            """UPDATE pet_record SET escalation_needed = CASE WHEN escalation_needed = 'True' THEN 'yes' ELSE 'no' END WHERE escalation_needed NOT IN ('yes', 'no') OR escalation_needed IS NULL""")
        self.env.cr.execute("UPDATE pet_record SET priority = '2' WHERE priority = '3'")
        self.env.cr.execute(
            """UPDATE pet_record SET task_status = 'on_track' WHERE task_status NOT IN ('no_set', 'overdue', 'today', 'on_track')""")

    def action_create_followup(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Log Follow-up',
            'res_model': 'pet.followup.line',
            'view_mode': 'form',
            'context': {
                'default_pet_record_id': self.id,
                'default_contact_date': fields.Date.context_today(self),
                'default_actual_next_followup_date': fields.Date.context_today(self),
                'default_category_id': self.category_id.id,
                'default_subcategory_id': self.subcategory_id.id,
                'default_patient_status': self.patient_status.id,
                'default_outcome_status': self.outcome_status,
                'default_reason_not_starting_stopping': self.reason_not_starting_stopping,
                'default_pain_walking_score': self.pain_walking_score,
                'default_pain_resting_score': self.pain_resting_score,
                'default_satisfaction_score': self.satisfaction_score,
                'default_mobility_status': self.mobility_status,
                'default_therapy_kit_status': self.therapy_kit_status,
                'default_discount_offered': self.discount_offered,
            },
            'target': 'new',
        }

    def _compute_bm_offer(self):
        for rec in self:
            latest_bm_log = self.env['bm.followup.log'].search([('patient_id', '=', rec.patient_id.id)],
                                                               order='timestamp desc', limit=1)
            rec.latest_bm_offer_price = latest_bm_log.offered_price if latest_bm_log else 0.0
            rec.latest_bm_therapies = latest_bm_log.total_therapies_included if latest_bm_log else 0

    @api.constrains('pain_walking_score', 'pain_resting_score', 'satisfaction_score')
    def _check_scores_range(self):
        for rec in self:
            if rec.pain_walking_score < 0 or rec.pain_walking_score > 10: raise ValidationError(
                "The 'Pain While Walking' score must be between 0 and 10.")
            if rec.pain_resting_score < 0 or rec.pain_resting_score > 10: raise ValidationError(
                "The 'Pain While Resting' score must be between 0 and 10.")
            if rec.satisfaction_score < 0 or rec.satisfaction_score > 10: raise ValidationError(
                "The 'Satisfaction Score' must be between 0 and 10.")

    @api.depends('category_id', 'subcategory_id')
    def _compute_allowed_statuses(self):
        for rec in self:
            cat = (rec.category_id.name or "").strip().lower()
            allowed_names = []

            if not cat:
                allowed_names = ['Continue Treatment', 'Planning to Stop', 'Stopped', 'Not Enrolled', 'Completed',
                                 'Maintenance']
            elif cat == 'active':
                allowed_names = ['Continue Treatment']
            elif cat == 'drop-off':
                allowed_names = ['Planning to Stop', 'Stopped']
            # AIRTIGHT FALLBACK: Catches both variations
            elif cat in ['not enrolled', 'not started']:
                allowed_names = ['Not Enrolled']
            elif cat == 'completed':
                allowed_names = ['Completed', 'Maintenance']

            if allowed_names:
                statuses = self.env['pet.patient.status'].search([('name', 'in', allowed_names)])
                rec.allowed_status_ids = statuses.ids
            else:
                rec.allowed_status_ids = False

    @api.onchange('category_id')
    def _onchange_category_id(self):
        """Clears subcategory and status when category changes"""
        self.subcategory_id = False
        self.patient_status = False

    @api.onchange('subcategory_id')
    def _onchange_subcategory_id(self):
        """Clears status when subcategory changes to prevent mismatches"""
        self.patient_status = False

    @api.depends('last_contact_date', 'actual_next_followup_date', 'subcategory_id', 'category_id',
                 'pain_walking_score', 'pain_resting_score', 'satisfaction_score', 'remarks')
    def _compute_all_metrics(self):
        today = fields.Date.today()
        for rec in self:
            rec.days_since_last_contact = (today - rec.last_contact_date).days if rec.last_contact_date else 0
            overdue = max(0, (today - rec.actual_next_followup_date).days) if rec.actual_next_followup_date else 0
            rec.followup_overdue = overdue

            sub_name = (rec.subcategory_id.name or "").strip().lower()
            cat_name = (rec.category_id.name or "").strip().lower()
            max_pain = max(rec.pain_walking_score, rec.pain_resting_score)

            if sub_name == "drop-risk" or overdue >= 3 or max_pain >= 8:
                rec.priority = '2'
            elif sub_name == "irregular" or overdue >= 1 or max_pain >= 6 or cat_name == "drop-off":
                rec.priority = '1'
            else:
                rec.priority = '0'

            rem = (rec.remarks or "").strip().lower()
            if (rec.priority == '2' or (
                    cat_name == 'completed' and rec.satisfaction_score < 7) or rem == 'lost trust'):
                rec.escalation_needed = 'yes'
            else:
                rec.escalation_needed = 'no'

            if not rec.actual_next_followup_date:
                rec.task_status = 'no_set'
            elif overdue > 0:
                rec.task_status = 'overdue'
            elif rec.actual_next_followup_date == today:
                rec.task_status = 'today'
            else:
                rec.task_status = 'on_track'

    @api.depends('category_id', 'subcategory_id', 'last_contact_date')
    def _compute_followup_logic(self):
        for rec in self:
            days = 0
            cat = (rec.category_id.name or "").strip().lower()
            sub_cat = (rec.subcategory_id.name or "").strip().lower()

            if cat == 'active':
                days = 2 if sub_cat == 'irregular' else 7
            elif cat == 'drop-off':
                days = 1 if sub_cat == 'drop-risk' else 3
            # AIRTIGHT FALLBACK: Catches both variations
            elif cat in ['not enrolled', 'not started']:
                days = 1 if sub_cat == 'hot' else (2 if sub_cat == 'warm' else (7 if sub_cat == 'cold' else 3))
            elif cat == 'completed':
                days = 7 if sub_cat == 'unsatisfied' else 30
            else:
                days = 0

            rec.suggested_followup_days = days
            rec.recommended_next_followup = rec.last_contact_date + timedelta(
                days=days) if rec.last_contact_date and days > 0 else False

    @api.onchange('patient_id')
    def _onchange_patient_id_dates(self):
        for rec in self:
            if rec.patient_id:
                rec.start_date = rec.patient_id.enroll_date
                last_session = self.env['patient.session'].search([('patient_id', '=', rec.patient_id.id)],
                                                                  order='session_date desc', limit=1)
                rec.last_visit_date = last_session.session_date if last_session and last_session.session_date else rec.patient_id.enroll_date
            else:
                rec.start_date = False
                rec.last_visit_date = False

    @api.model
    def _cron_update_daily_metrics(self):
        records_to_update = self.search([('actual_next_followup_date', '!=', False)])
        if records_to_update:
            records_to_update._compute_all_metrics()
            records_to_update.action_sync_retroactive_todos()

    def action_sync_retroactive_todos(self):
        for rec in self:
            target_date = rec.recommended_next_followup or rec.actual_next_followup_date
            if target_date:
                existing = self.env['mail.activity'].search([
                    ('res_model', '=', 'pet.record'),
                    ('res_id', '=', rec.id),
                    ('summary', '=', 'Patient Follow-up Due')
                ])
                if not existing:
                    last_log = self.env['pet.followup.line'].search(
                        [('pet_record_id', '=', rec.id)], order='create_date desc', limit=1)
                    agent_id = last_log.user_id.id if last_log else rec.advisor_id.id

                    rec.activity_schedule(
                        activity_type_id=self.env.ref('mail.mail_activity_data_todo').id,
                        user_id=agent_id,
                        note='<strong>Scheduled Follow-up:</strong> Retroactively synced follow-up task.',
                        summary='Patient Follow-up Due',
                        date_deadline=target_date
                    )


class BMFollowupLog(models.Model):
    _name = 'bm.followup.log'
    _description = 'BM Follow-up Log'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, ondelete='cascade', tracking=True)
    user_id = fields.Many2one('res.users', string="Advisor/BM", default=lambda self: self.env.user, readonly=True, tracking=True)

    offered_price = fields.Float(string="Offered Price", tracking=True)
    total_therapies_included = fields.Integer(string="Total Therapies Included", tracking=True)

    pet_agent_id = fields.Many2one('res.users', string="PET Agent (Revenue Tracker)", tracking=True)
    pet_discount_offered = fields.Float(string="PET Discount Applied (%)", tracking=True, readonly=True)
    final_agreed_price = fields.Float(string="Final Price (After Discount)", compute="_compute_final_price", store=True)

    ticket_ids = fields.One2many('pet.escalation.ticket', 'bm_log_id', string="Escalation Tickets")

    remarks = fields.Text(string="Remarks", tracking=True)
    action_taken = fields.Text(string="Action History", required=True, tracking=True)
    timestamp = fields.Datetime(string="Timestamp", default=fields.Datetime.now, readonly=True, tracking=True)

    is_locked = fields.Boolean(string="Locked", compute="_compute_is_locked")

    @api.depends('offered_price', 'pet_discount_offered')
    def _compute_final_price(self):
        for rec in self:
            price = rec.offered_price or 0.0
            discount_percentage = rec.pet_discount_offered or 0.0
            rec.final_agreed_price = price - (price * (discount_percentage / 100.0))

    def action_back_to_patient(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Patient Profile',
            'res_model': 'clinic.patient',
            'view_mode': 'form',
            'res_id': self.patient_id.id,
            'target': 'current',
        }

    def _compute_is_locked(self):
        for rec in self:
            if rec.timestamp:
                rec.is_locked = (fields.Datetime.now() - rec.timestamp).total_seconds() > 86400
            else:
                rec.is_locked = False


class PatientInherit(models.Model):
    _inherit = 'clinic.patient'

    pet_record_ids = fields.One2many('pet.record', 'patient_id', string="PET Records")
    pet_last_contact_date = fields.Date(string="PET Last Contact", compute="_compute_pet_dates", store=True)
    pet_next_followup = fields.Date(string="PET Next Follow-up", compute="_compute_pet_dates", store=True)

    @api.depends('pet_record_ids.last_contact_date', 'pet_record_ids.actual_next_followup_date')
    def _compute_pet_dates(self):
        for rec in self:
            latest_pet = self.env['pet.record'].search([('patient_id', '=', rec.id)], order='create_date desc', limit=1)
            rec.pet_last_contact_date = latest_pet.last_contact_date if latest_pet else False
            rec.pet_next_followup = latest_pet.actual_next_followup_date if latest_pet else False

    def action_open_pet_tracker(self):
        last_session = self.env['patient.session'].search([('patient_id', '=', self.id)], order='session_date desc', limit=1)
        last_visit = last_session.session_date if last_session and last_session.session_date else self.enroll_date

        existing_records = self.env['pet.record'].search([('patient_id', '=', self.id)], order='create_date desc')

        if len(existing_records) > 1:
            master_record = existing_records[0]
            duplicates = existing_records[1:]

            for dup in duplicates:
                self.env['pet.followup.line'].create({
                    'pet_record_id': master_record.id,
                    'contact_date': dup.last_contact_date or dup.create_date.date(),
                    'user_id': dup.advisor_id.id if dup.advisor_id else self.env.user.id,
                    'actual_next_followup_date': dup.actual_next_followup_date or fields.Date.context_today(self),
                    'recommended_next_followup': dup.recommended_next_followup,
                    'category_id': dup.category_id.id if dup.category_id else False,
                    'subcategory_id': dup.subcategory_id.id if dup.subcategory_id else False,
                    'patient_status': dup.patient_status.id if dup.patient_status else False,
                    'outcome_status': dup.outcome_status,
                    'reason_not_starting_stopping': dup.reason_not_starting_stopping,
                    'pain_walking_score': dup.pain_walking_score or 0,
                    'pain_resting_score': dup.pain_resting_score or 0,
                    'satisfaction_score': dup.satisfaction_score or 0,
                    'mobility_status': dup.mobility_status,
                    'therapy_kit_status': dup.therapy_kit_status,
                    'referral_given': dup.referral_given,
                    'review_given': dup.review_given,
                    'referral_name': dup.referral_name,
                    'referral_contact': dup.referral_contact,
                    'review_link': dup.review_link,
                    'action_taken': dup.action_taken or False,
                    'remarks': dup.remarks or False,
                })
            duplicates.unlink()
            existing_records = master_record

        if not existing_records:
            existing_records = self.env['pet.record'].create({
                'patient_id': self.id,
                'start_date': self.enroll_date,
                'last_visit_date': last_visit,
            })

        return {
            'type': 'ir.actions.act_window',
            'name': 'PET Tracker',
            'res_model': 'pet.record',
            'view_mode': 'form',
            'res_id': existing_records.id,
            'target': 'current',
        }

    def action_open_bm_followup(self):
        latest_log = self.env['bm.followup.log'].search([('patient_id', '=', self.id)], order='timestamp desc', limit=1)

        if latest_log:
            return {
                'type': 'ir.actions.act_window',
                'name': 'BM Follow-up Log',
                'res_model': 'bm.followup.log',
                'view_mode': 'form',
                'res_id': latest_log.id,
                'target': 'current',
            }
        else:
            return {
                'type': 'ir.actions.act_window',
                'name': 'New BM Follow-up',
                'res_model': 'bm.followup.log',
                'view_mode': 'form',
                'context': {
                    'default_patient_id': self.id,
                },
                'target': 'current',
            }

    @api.model_create_multi
    def create(self, vals_list):
        patients = super(PatientInherit, self).create(vals_list)

        for patient in patients:
            patient.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=self.env.user.id,
                summary='🛑 BM TASK: Log Initial Quote & Therapies',
                note=f'Patient <b>{patient.name}</b> just registered. Please contact them and log the offered price in the BM Follow-up Log.'
            )

            pet_rec = self.env['pet.record'].create({
                'patient_id': patient.id,
                'start_date': patient.enroll_date or fields.Date.context_today(self),
                'last_visit_date': patient.enroll_date or fields.Date.context_today(self),
            })

            pet_rec.activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=self.env.user.id,
                summary='📞 PET TASK: Initiate First Contact',
                note=f'New Patient <b>{patient.name}</b> registered. Please initiate the follow-up conversion and experience tracking protocol.'
            )

        return patients

    def action_open_consent(self):
        return super(PatientInherit, self).action_open_consent()

    def action_open_patient_xray(self):
        return super(PatientInherit, self).action_open_patient_xray()