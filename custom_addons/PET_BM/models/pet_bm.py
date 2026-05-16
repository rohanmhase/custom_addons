from odoo import models, fields, api
from odoo.exceptions import ValidationError
from datetime import date, timedelta


# --- ADDED: The missing model for the dynamic statuses ---
class PETPatientStatus(models.Model):
    _name = 'pet.patient.status'
    _description = 'Patient Treatment Status'

    name = fields.Char(string="Status Name", required=True)
    active = fields.Boolean(default=True)

    def init(self):
        """
        This automatically injects the default statuses into the database
        when the module is upgraded, keeping everything in Python!
        """
        default_statuses = [
            'Active',
            'Continue Treatment',
            'Planning to Stop',
            'Stopped',
            'Not Started',
            'Completed',
            'Maintenance'
        ]
        for status in default_statuses:
            # Check if it already exists so we don't create duplicates on every upgrade
            if not self.search([('name', '=', status)]):
                self.create({'name': status})


class PETCategory(models.Model):
    _name = 'pet.category'
    _description = 'PET Category'
    name = fields.Char(string="Category Name", required=True)


class PETSubCategory(models.Model):
    _name = 'pet.subcategory'
    _description = 'PET Sub Category'
    category_id = fields.Many2one('pet.category', string="Category", required=True, ondelete='cascade')
    name = fields.Char(string="Sub Category Name", required=True)


class PETRecord(models.Model):
    _name = 'pet.record'
    _description = 'Patient Experience Tracker'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # --- RELATIONAL FIELDS (Patient Info) ---
    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, ondelete='cascade', tracking=True)
    patient_mrn = fields.Char(related="patient_id.mrn", string="MRN", readonly=True)
    clinic_id = fields.Many2one('clinic.clinic', string="Clinic Name", related="patient_id.clinic_id", store=True)
    advisor_id = fields.Many2one('res.users', string="Advisor Name", default=lambda self: self.env.user, tracking=True)
    phone_number = fields.Char(related="patient_id.phone", string="Phone Number", readonly=True)
    alternate_number = fields.Integer(string='Alternate Phone Number', tracking=True)

    # --- THE RELATIONAL MATRIX ---
    category_id = fields.Many2one('pet.category', string="Category", tracking=True)
    subcategory_id = fields.Many2one('pet.subcategory', string="Sub Category",
                                     domain="[('category_id', '=', category_id)]", tracking=True)

    # --- FIXED: Many2one syntax does not accept a list of tuples ---
    patient_status = fields.Many2one(
        'pet.patient.status',
        string="Current Treatment Status",
        tracking=True
    )
    allowed_status_ids = fields.Many2many(
        'pet.patient.status',
        compute='_compute_allowed_statuses',
        store=False
    )

    reason_not_starting_stopping = fields.Text(string="Reason for Not Starting / Stopping", tracking=True)
    therapy_kit_status = fields.Selection([
        ('yes', 'Yes'),
        ('no', 'No'),
        ('maybe', 'Maybe')
    ], string="Home Therapy Kit", tracking=True)

    # --- DATES ---
    start_date = fields.Date(string="Start Date", tracking=True)
    last_visit_date = fields.Date(string="Last Visit Date", tracking=True)
    last_contact_date = fields.Date(string="Last Contact Date", tracking=True)
    actual_next_followup_date = fields.Date(string="Actual Next Follow-up Date", tracking=True)

    suggested_followup_days = fields.Integer(string="Suggested Follow-up Days", compute="_compute_followup_logic",
                                             store=True, readonly=True)
    recommended_next_followup = fields.Date(string="Recommended Next Follow-up", compute="_compute_followup_logic",
                                            store=True, readonly=True)

    # --- METRICS & OUTCOMES ---
    pain_walking_score = fields.Integer(string="Pain While Walking (0-10)", tracking=True)
    pain_resting_score = fields.Integer(string="Pain While Resting (0-10)", tracking=True)
    mobility_status = fields.Selection(
        [('Severe Limitation', 'Severe'), ('Moderate Limitation', 'Moderate'), ('Mild Limitation', 'Mild'),
         ('Independent', 'Independent')],
        string="Mobility Status", tracking=True)
    satisfaction_score = fields.Integer(string="Satisfaction Score (0-10)", tracking=True)
    referral_given = fields.Boolean(string="Referral Given", tracking=True)
    review_given = fields.Boolean(string="Review Given", tracking=True)

    referral_name = fields.Char(string="Referral Name", tracking=True)
    referral_contact = fields.Char(string="Referral Number", tracking=True)
    review_link = fields.Char(string="Review Link/Details", tracking=True)

    action_taken = fields.Text(string="Action Taken", tracking=True)
    remarks = fields.Text(string="Remarks", tracking=True)
    outcome_status = fields.Selection(
        [('On Track', 'On Track'), ('Improving', 'Improving'), ('Plateau', 'Plateau'), ('Stopped', 'Stopped')
            , ('completed', 'Completed')],
        string="Outcome Status", tracking=True)

    # Ensure all five fields have both store=True and compute_sudo=True
    days_since_last_contact = fields.Integer(
        string="Days Since Last Contact",
        compute="_compute_all_metrics",
        store=True, readonly=True, compute_sudo=True
    )
    followup_overdue = fields.Integer(
        string="Follow-up Overdue (Days)",
        compute="_compute_all_metrics",
        store=True, readonly=True, compute_sudo=True
    )
    priority = fields.Selection([
        ('0', 'Low'),
        ('1', 'Medium'),
        ('2', 'High')
    ], string="Priority", compute="_compute_all_metrics", store=True, readonly=True, compute_sudo=True)

    escalation_needed = fields.Selection([
        ('no', 'No'),
        ('yes', 'Yes')
    ], string="Escalation Needed", compute="_compute_all_metrics", store=True, readonly=True, compute_sudo=True)

    task_status = fields.Selection([
        ('no_set', 'No follow-up set'),
        ('overdue', 'Overdue Follow-up'),
        ('today', 'Due Today'),
        ('on_track', 'On Track')
    ], string="Task Status", compute="_compute_all_metrics", store=True, readonly=True, compute_sudo=True)

    def init(self):
        """
        This runs automatically during module upgrade to fix existing records
        and prevent the OwlError/Selection crash.
        """
        # 1. Convert old Boolean escalation to Selection strings
        self.env.cr.execute("""
            UPDATE pet_record 
            SET escalation_needed = CASE 
                WHEN escalation_needed = 'True' THEN 'yes' 
                ELSE 'no' 
            END 
            WHERE escalation_needed NOT IN ('yes', 'no') OR escalation_needed IS NULL
        """)

        # 2. Fix old Priority keys (Mapping '3'/'Critical' to '2'/'High')
        self.env.cr.execute("UPDATE pet_record SET priority = '2' WHERE priority = '3'")

        # 3. Reset Task Status keys to a valid new value
        self.env.cr.execute("""
            UPDATE pet_record 
            SET task_status = 'on_track' 
            WHERE task_status NOT IN ('no_set', 'overdue', 'today', 'on_track')
        """)

    # ==========================================
    # AUTOMATIONS
    # ==========================================

    @api.constrains('pain_walking_score', 'pain_resting_score', 'satisfaction_score')
    def _check_scores_range(self):
        for rec in self:
            if rec.pain_walking_score < 0 or rec.pain_walking_score > 10:
                raise ValidationError("The 'Pain While Walking' score must be between 0 and 10.")

            if rec.pain_resting_score < 0 or rec.pain_resting_score > 10:
                raise ValidationError("The 'Pain While Resting' score must be between 0 and 10.")

            if rec.satisfaction_score < 0 or rec.satisfaction_score > 10:
                raise ValidationError("The 'Satisfaction Score' must be between 0 and 10.")

    @api.depends('category_id', 'subcategory_id')
    def _compute_allowed_statuses(self):
        for rec in self:
            cat = rec.category_id.name if rec.category_id else False
            sub = rec.subcategory_id.name if rec.subcategory_id else False

            allowed_names = []

            # 1. If NO Category is selected, show all options so the dropdown isn't dead
            if not cat:
                allowed_names = [
                    'Continue Treatment', 'Planning to Stop', 'Stopped',
                    'Not Started', 'Completed', 'Maintenance'
                ]

            # 2. Logic for 'Active' Category (Visible with or without subcategory)
            elif cat == 'Active':
                allowed_names = ['Continue Treatment','Not Started']

            # 3. Logic for 'Drop-off' Category
            elif cat == 'Drop-off':
                if sub == 'Drop-risk':
                    allowed_names = ['Planning to Stop', 'Stopped']
                else:
                    # Fallback if Drop-off is selected but subcategory is blank
                    allowed_names = ['Planning to Stop', 'Stopped']

            # 4. Logic for 'Not Enrolled' Category (Visible with or without subcategory)
            elif cat == 'Not Enrolled':
                allowed_names = ['Not Started','Maintenance']

            # 5. Logic for 'Completed' Category (Visible with or without subcategory)
            elif cat == 'Completed':
                allowed_names = ['Completed', 'Maintenance']

            # Apply the results to the invisible field
            if allowed_names:
                statuses = self.env['pet.patient.status'].search([('name', 'in', allowed_names)])
                rec.allowed_status_ids = statuses.ids
            else:
                rec.allowed_status_ids = False

    @api.onchange('category_id', 'subcategory_id')
    def _clear_status_on_change(self):
        # Simply wipe the current selection clean if the user changes the category
        self.patient_status = False

    @api.depends(
        'last_contact_date', 'actual_next_followup_date', 'subcategory_id',
        'category_id', 'pain_walking_score', 'pain_resting_score',
        'satisfaction_score', 'remarks'
    )
    def _compute_all_metrics(self):
        today = fields.Date.today()
        for rec in self:
            # A. Calculate Days Since Last Contact & Overdue
            rec.days_since_last_contact = (today - rec.last_contact_date).days if rec.last_contact_date else 0

            overdue = 0
            if rec.actual_next_followup_date:
                diff = (today - rec.actual_next_followup_date).days
                overdue = max(0, diff)
            rec.followup_overdue = overdue

            # B. Priority Logic (Using original keys '0', '1', '2')
            sub_name = rec.subcategory_id.name if rec.subcategory_id else ""
            cat_name = rec.category_id.name if rec.category_id else ""
            max_pain = max(rec.pain_walking_score, rec.pain_resting_score)

            if sub_name == "Drop-risk" or overdue >= 3 or max_pain >= 8:
                rec.priority = '2'  # High
            elif sub_name == "Irregular" or overdue >= 1 or max_pain >= 6 or cat_name == "Drop-off":
                rec.priority = '1'  # Medium
            else:
                rec.priority = '0'  # Low

            # C. Escalation Needed Logic
            if (rec.priority == '2' or
                    (cat_name == 'Completed' and rec.satisfaction_score < 7) or
                    rec.remarks == 'Lost trust'):
                rec.escalation_needed = 'yes'
            else:
                rec.escalation_needed = 'no'

            # D. Task Status Logic
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
            cat = rec.category_id.name if rec.category_id else ""
            sub_cat = rec.subcategory_id.name if rec.subcategory_id else ""

            if not cat:
                rec.suggested_followup_days = 0
                rec.recommended_next_followup = False
                continue

            if cat == 'Active':
                if sub_cat == 'Regular':
                    days = 7
                elif sub_cat == 'Irregular':
                    days = 2
                elif sub_cat == 'Drop-risk':
                    days = 1
                else:
                    days = 7
            elif cat == 'Drop-off':
                days = 3
            elif cat == 'Not Enrolled':
                if sub_cat == 'Hot':
                    days = 1
                elif sub_cat == 'Warm':
                    days = 2
                elif sub_cat == 'Cold':
                    days = 7
                else:
                    days = 3
            elif cat == 'Completed':
                if sub_cat == 'Unsatisfied':
                    days = 7
                else:
                    days = 30
            else:
                days = 0

            rec.suggested_followup_days = days

            if rec.last_contact_date and days > 0:
                rec.recommended_next_followup = rec.last_contact_date + timedelta(days=days)
            else:
                rec.recommended_next_followup = False

    @api.onchange('patient_id')
    def _onchange_patient_id_dates(self):
        for rec in self:
            if rec.patient_id:
                # 1. Start Date is strictly the Enrollment Date
                rec.start_date = rec.patient_id.enroll_date

                # 2. Search the patient.session table for their most recent therapy session
                # (Assuming the relational field linking session to patient is named 'patient_id')
                last_session = self.env['patient.session'].search(
                    [('patient_id', '=', rec.patient_id.id)],
                    order='session_date desc',
                    limit=1
                )

                # If a session exists, use its date. Otherwise, fallback to enrollment date.
                if last_session and last_session.session_date:
                    rec.last_visit_date = last_session.session_date
                else:
                    rec.last_visit_date = rec.patient_id.enroll_date
            else:
                rec.start_date = False
                rec.last_visit_date = False


# --- TOTALLY SEPARATED BM FOLLOW-UP DATA ---
class BMFollowupLog(models.Model):
    _name = 'bm.followup.log'
    _description = 'BM Follow-up Log'
    _order = 'create_date desc'

    # This single line turns on Odoo's tracking engine for this model
    _inherit = ['mail.thread', 'mail.activity.mixin']

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, ondelete='cascade', tracking=True)
    user_id = fields.Many2one('res.users', string="Advisor/BM", default=lambda self: self.env.user, readonly=True,
                              tracking=True)

    # Kept it simple: Just a text box for the history
    action_taken = fields.Text(string="Action History", required=True, tracking=True)
    timestamp = fields.Datetime(string="Timestamp", default=fields.Datetime.now, readonly=True, tracking=True)


class PatientInherit(models.Model):
    _inherit = 'clinic.patient'

    def action_open_pet_tracker(self):
        # Search for the most recent therapy session for this specific patient
        last_session = self.env['patient.session'].search(
            [('patient_id', '=', self.id)],
            order='session_date desc',
            limit=1
        )

        # Determine the correct last visit date based on the search
        last_visit = last_session.session_date if last_session and last_session.session_date else self.enroll_date

        return {
            'type': 'ir.actions.act_window',
            'name': 'PET Tracker',
            'res_model': 'pet.record',
            'view_mode': 'tree,form',
            'domain': [('patient_id', '=', self.id)],
            'context': {
                'default_patient_id': self.id,
                'default_start_date': self.enroll_date,
                'default_last_visit_date': last_visit,
            },
            'target': 'current',
        }

    def action_open_bm_followup(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'BM Follow-up Logs',
            'res_model': 'bm.followup.log',
            'view_mode': 'tree,form',
            'domain': [('patient_id', '=', self.id)],
            'context': {'default_patient_id': self.id},
            'target': 'current',
        }

    # ========================================================
    # ADD THESE TWO FUNCTIONS TO FIX THE XML VALIDATION CRASH
    # ========================================================
    def action_open_consent(self):
        """ Calls the original consent form logic from the parent module """
        return super(PatientInherit, self).action_open_consent()

    def action_open_patient_xray(self):
        """ Calls the original X-Ray logic from the parent module """
        return super(PatientInherit, self).action_open_patient_xray()