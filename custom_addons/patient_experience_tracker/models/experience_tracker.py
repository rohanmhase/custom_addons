from odoo import models, fields, api
from datetime import timedelta


class PatientExperienceTracker(models.Model):
    _name = 'patient.experience.tracker'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Patient Experience Log'

    # 1. CORE RELATIONS (Read-Only)
    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    clinic_id = fields.Many2one(related='patient_id.clinic_id', string="Clinic", readonly=True, store=True)
    advisor_id = fields.Many2one('res.users', string="Advisor Name", default=lambda self: self.env.user, tracking=True)

    phone = fields.Char(related='patient_id.phone', string="Phone", readonly=True)
    mrn = fields.Char(related='patient_id.mrn', string="MRN", readonly=True)
    pain_types = fields.Char(related='patient_id.pain_types', string="Pain Focus", readonly=True)
    total_sessions = fields.Integer(related='patient_id.total_sessions', string="Total Sessions", readonly=True)

    # 2. STATUS & CATEGORY
    category = fields.Selection([
        ('Active', 'Active'), ('Drop-off', 'Drop-off'),
        ('Not Enrolled', 'Not Enrolled'), ('Completed', 'Completed')
    ], string='Category', tracking=True)

    sub_category = fields.Selection([
        ('Regular', 'Regular'), ('Irregular', 'Irregular'),
        ('Drop-risk', 'Drop-risk'),
        ('Hot', 'Hot'), ('Warm', 'Warm'), ('Cold', 'Cold'),
        ('Happy', 'Happy'), ('Neutral', 'Neutral'), ('Unsatisfied', 'Unsatisfied')
    ], string='Sub Category', tracking=True)

    priority = fields.Selection([('Low', 'Low'), ('Medium', 'Medium'), ('High', 'High')], string="Priority",
                                default="Low", tracking=True)

    # 3. MEDICAL & TREATMENT INFO
    treatment_status = fields.Selection([
        ('Not Started', 'Not Started'), ('Continue Treatment', 'Continue Treatment'),
        ('Planning to Stop', 'Planning to Stop'), ('Stopped', 'Stopped'),
        ('Completed', 'Completed'), ('Maintenance', 'Maintenance')
    ], string="Current Treatment Status")

    reason_not_starting = fields.Char(string="Reason for Not Starting / Stopping")
    home_therapy = fields.Selection([('Yes', 'Yes'), ('No', 'No'), ('Maybe', 'Maybe')], string="Home Therapy Kit")
    outcome_status = fields.Selection([
        ('On Track', 'On Track'), ('Improving', 'Improving'), ('Plateau', 'Plateau'),
        ('Stopped', 'Stopped'), ('Completed', 'Completed'), ('Not connected', 'Not connected')
    ], string="Outcome Status")
    mobility_status = fields.Selection([
        ('Severe Limitation', 'Severe Limitation'), ('Moderate Limitation', 'Moderate Limitation'),
        ('Mild Limitation', 'Mild Limitation'), ('Independent', 'Independent')
    ], string="Mobility Status")
    pain_score = fields.Selection([(str(i), str(i)) for i in range(11)], string='Pain Score (0-10)')

    # 4. DATES & FOLLOW-UPS
    start_date = fields.Date(string="Start Date")
    last_visit_date = fields.Date(string="Last Visit Date")
    last_contact_date = fields.Date(string='Last Contact Date', default=fields.Date.context_today, tracking=True)
    next_followup_date = fields.Date(string='Recommended Next Follow-up', compute='_compute_followup', store=True,
                                     tracking=True)
    actual_followup_date = fields.Date(string='Actual Next Follow-up Date', tracking=True)

    days_since_last_contact = fields.Integer(string="Days Since Last Contact", compute="_compute_days_since")

    followup_status = fields.Selection([
        ('overdue', 'Overdue'), ('today', 'Due Today'), ('planned', 'Planned')
    ], string="Urgency", compute='_compute_followup_status')

    # 5. METRICS & REMARKS
    satisfaction_score = fields.Selection([(str(i), str(i)) for i in range(11)], string="Satisfaction Score (0-10)")
    referral_given = fields.Boolean(string="Referral Given")
    review_given = fields.Boolean(string="Review Given")
    action_taken = fields.Char(string="Action Taken")
    remarks = fields.Text(string="Remarks")
    escalation_needed = fields.Boolean(string="Escalation Needed", tracking=True)
    task_status = fields.Selection([('On Track', 'On Track'), ('Pending', 'Pending'), ('Closed', 'Closed')],
                                   default='On Track', string="Task Status")

    # --- COMPUTED LOGIC ---

    @api.onchange('category', 'sub_category')
    def _onchange_category_enforce_rules(self):
        for record in self:
            # 1. Enforce Treatment Status Rules
            if record.category == 'Not Enrolled':
                record.treatment_status = 'Not Started'
            elif record.category == 'Drop-off':
                record.treatment_status = False
                # Auto-fill drop-risk if Drop-off is selected
                if not record.sub_category or record.sub_category != 'Drop-risk':
                    record.sub_category = 'Drop-risk'

            # 2. Enforce Home Therapy Rules
            if record.category in ['Drop-off', 'Completed']:
                record.home_therapy = False

            # 3. REJECT INVALID SUB-CATEGORIES
            if record.category and record.sub_category:
                warning_msg = ""

                if record.category == 'Active' and record.sub_category not in ['Regular', 'Irregular']:
                    warning_msg = "Active patients can only be marked as 'Regular' or 'Irregular'."

                elif record.category == 'Drop-off' and record.sub_category != 'Drop-risk':
                    warning_msg = "Drop-off patients must be marked as 'Drop-risk'."

                elif record.category == 'Not Enrolled' and record.sub_category not in ['Hot', 'Warm', 'Cold']:
                    warning_msg = "Not Enrolled leads must be marked as 'Hot', 'Warm', or 'Cold'."

                elif record.category == 'Completed' and record.sub_category not in ['Happy', 'Neutral', 'Unsatisfied']:
                    warning_msg = "Completed patients must be marked as 'Happy', 'Neutral', or 'Unsatisfied'."

                # If they broke a rule, erase their choice and show the warning
                if warning_msg:
                    record.sub_category = False
                    return {
                        'warning': {
                            'title': 'Invalid Selection',
                            'message': warning_msg
                        }
                    }

    @api.depends('last_contact_date')
    def _compute_days_since(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.last_contact_date:
                diff = today - record.last_contact_date
                record.days_since_last_contact = diff.days
            else:
                record.days_since_last_contact = 0

    @api.depends('next_followup_date')
    def _compute_followup_status(self):
        today = fields.Date.context_today(self)
        for record in self:
            if not record.next_followup_date:
                record.followup_status = 'planned'
            elif record.next_followup_date < today:
                record.followup_status = 'overdue'
            elif record.next_followup_date == today:
                record.followup_status = 'today'
            else:
                record.followup_status = 'planned'

    @api.depends('category', 'sub_category', 'last_contact_date')
    def _compute_followup(self):
        for record in self:
            days = 0
            if record.category == 'Active':
                if record.sub_category == 'Drop-risk':
                    days = 1
                elif record.sub_category == 'Irregular':
                    days = 2  # Matches Excel
                else:
                    days = 7
            elif record.category == 'Drop-off':
                days = 3
            elif record.category == 'Not Enrolled':
                if record.sub_category == 'Hot':
                    days = 1
                elif record.sub_category == 'Warm':
                    days = 2
                elif record.sub_category == 'Cold':
                    days = 7
                else:
                    days = 3  # Matches Excel default
            elif record.category == 'Completed':
                if record.sub_category == 'Unsatisfied':
                    days = 7  # Matches Excel
                else:
                    days = 30

            if record.last_contact_date:
                record.next_followup_date = record.last_contact_date + timedelta(days=days)
            else:
                record.next_followup_date = False
