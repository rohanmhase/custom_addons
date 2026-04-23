from odoo import models, fields, api, _
from datetime import timedelta
from odoo.exceptions import AccessError, UserError, ValidationError


# ==========================================
# 1. THE NEW DICTIONARY MODEL
# ==========================================
class PatientExperienceSubcategory(models.Model):
    _name = 'patient.experience.subcategory'
    _description = 'Patient Experience Subcategory'

    name = fields.Char(string='Name', required=True)
    parent_category = fields.Selection([
        ('Not Enrolled', 'Not Enrolled'), ('Active', 'Active'), ('Drop-off', 'Drop-off'),
         ('Completed', 'Completed')
    ], string='Parent Category', required=True, tracking=True)


# ==========================================
# 2. THE MAIN TRACKER MODEL
# ==========================================
class PatientExperienceTracker(models.Model):
    _name = 'patient.experience.tracker'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Patient Experience Log'

    _sql_constraints = [
        ('unique_patient_tracker', 'unique(patient_id)',
         'This patient already has an Experience Tracker! Please go back and use the Search bar to find their existing record instead of creating a new one.')
    ]

    # 1. CORE RELATIONS
    patient_id = fields.Many2one('clinic.patient', string='Patient', required=True)
    clinic_id = fields.Many2one(related='patient_id.clinic_id', string="Clinic", readonly=True, store=True)
    advisor_id = fields.Many2one('res.users', string="Advisor Name", default=lambda self: self.env.user, tracking=True)

    phone = fields.Char(related='patient_id.phone', string="Phone", readonly=True)
    mrn = fields.Char(related='patient_id.mrn', string="MRN", readonly=True)
    pain_types = fields.Char(related='patient_id.pain_types', string="Conditions", readonly=True)
    total_sessions = fields.Integer(related='patient_id.total_sessions', string="Total Sessions", readonly=True)
    used_sessions = fields.Integer(compute='_compute_used_sessions', string="Used Sessions", readonly=True)


    @api.depends('patient_id.total_sessions', 'patient_id.remaining_sessions')
    def _compute_used_sessions(self):
        for record in self:
            if record.patient_id:
                record.used_sessions = record.patient_id.total_sessions - record.patient_id.remaining_sessions
            else:
                record.used_sessions = 0

    # 2. STATUS & CATEGORY
    category = fields.Selection([
        ('Active', 'Active'), ('Drop-off', 'Drop-off'),
        ('Not Enrolled', 'Not Enrolled'), ('Completed', 'Completed')
    ], string='Category', tracking=True, required=True)

    # *** FIXED: UPGRADED & RENAMED TO AVOID DB CRASH ***
    sub_category_id = fields.Many2one('patient.experience.subcategory', string='Sub Category', tracking=True,
                                      required=True)

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
    pain_score = fields.Selection([(str(i), str(i)) for i in range(11)], string='Pain Score (0-10)', required=True,
                                  tracking=True)

    # 4. DATES & FOLLOW-UPS
    start_date = fields.Date(string="Start Date", required=True, tracking=True)
    last_visit_date = fields.Date(string="Last Visit Date", required=True, tracking=True)
    last_contact_date = fields.Date(string='Last Contact Date', default=fields.Date.context_today, tracking=True,
                                    required=True, )
    next_followup_date = fields.Date(string='Recommended Next Follow-up', compute='_compute_followup', store=True,
                                     tracking=True)
    actual_followup_date = fields.Date(string='Actual Next Follow-up Date', tracking=True, required=True)

    days_since_last_contact = fields.Integer(string="Days Since Last Contact", compute="_compute_days_since")

    followup_status = fields.Selection([
        ('overdue', 'Overdue'), ('today', 'Due Today'), ('planned', 'Planned')
    ], string="Urgency", compute='_compute_followup_status')

    # 5. METRICS & REMARKS
    satisfaction_score = fields.Selection([(str(i), str(i)) for i in range(11)], string="Satisfaction Score (0-10)",
                                          required=True)
    referral_given = fields.Boolean(string="Referral Given", tracking=True)
    review_given = fields.Boolean(string="Review Given", tracking=True)
    action_taken = fields.Text(string="Action Taken", tracking=True)
    remarks = fields.Text(string="Remarks", tracking=True)
    escalation_needed = fields.Boolean(string="Escalation Needed", tracking=True)
    task_status = fields.Selection([('On Track', 'On Track'), ('Pending', 'Pending'), ('Closed', 'Closed')],
                                   default='On Track', string="Task Status")

    # --- COMPUTED LOGIC ---
    @api.onchange('category')
    def _onchange_category_enforce_rules(self):
        """Cleaned up automation without the warning boxes"""
        for record in self:
            # 1. Wipe sub_category clean so they are forced to pick from the new filtered list
            record.sub_category_id = False

            # 2. Enforce Treatment Status Rules
            if record.category == 'Not Enrolled':
                record.treatment_status = 'Not Started'
            elif record.category == 'Drop-off':
                record.treatment_status = False
                # Auto-find and attach "Drop-risk" from the dictionary
                drop_risk = self.env['patient.experience.subcategory'].search(
                    [('name', '=', 'Drop-risk'), ('parent_category', '=', 'Drop-off')], limit=1)
                if drop_risk:
                    record.sub_category_id = drop_risk.id

            # 3. Enforce Home Therapy Rules
            if record.category in ['Drop-off', 'Completed']:
                record.home_therapy = False

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

    @api.depends('category', 'sub_category_id', 'last_contact_date')
    def _compute_followup(self):
        for record in self:
            days = 0
            # Because it's a relational field now, we must look at '.name'
            sub_name = record.sub_category_id.name if record.sub_category_id else False

            if record.category == 'Active':
                if sub_name == 'Drop-risk':
                    days = 1
                elif sub_name == 'Irregular':
                    days = 2
                else:
                    days = 7
            elif record.category == 'Drop-off':
                days = 3
            elif record.category == 'Not Enrolled':
                if sub_name == 'Hot':
                    days = 1
                elif sub_name == 'Warm':
                    days = 2
                elif sub_name == 'Cold':
                    days = 7
                else:
                    days = 3
            elif record.category == 'Completed':
                if sub_name == 'Unsatisfied':
                    days = 7
                else:
                    days = 30

            if record.last_contact_date:
                record.next_followup_date = record.last_contact_date + timedelta(days=days)
            else:
                record.next_followup_date = False

    # ==========================================
    # SECURITY & ACCESS CONTROL
    # ==========================================
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If a regular user tries to assign it to someone else during creation
            if 'advisor_id' in vals and vals.get('advisor_id') and vals.get('advisor_id') != self.env.user.id:
                if not self.env.user.has_group('patient_experience_tracker.group_presales_manager'):
                    raise AccessError(
                        "Security Block: Only a Pre-Sales Manager can assign a lead to a different Advisor.")
        return super(PatientExperienceTracker, self).create(vals_list)

    def write(self, vals):
        # Only trigger the security check if the advisor_id is present in the save command
        if 'advisor_id' in vals:
            for record in self:
                # CRITICAL: Only trigger if the new advisor is ACTUALLY DIFFERENT from the current advisor
                if vals['advisor_id'] != record.advisor_id.id:
                    # Check if the user lacks the Pre-Sales Manager badge
                    if not self.env.user.has_group('patient_experience_tracker.group_presales_manager'):
                        raise AccessError("Security Block: You do not have permission to change the Advisor Name.")
        return super(PatientExperienceTracker, self).write(vals)

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))

