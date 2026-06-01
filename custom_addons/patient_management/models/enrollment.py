from odoo import models, fields, api, _
from datetime import datetime, timedelta, date
from odoo.exceptions import UserError, ValidationError


class Enrollment(models.Model):
    _name = 'patient.enrollment'
    _description = 'Patient Enrollment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one('res.users', string="BM", required=True, readonly=True, default=lambda self: self.env.user)
    enrollment_date = fields.Date(string="Enrollment Date", required=True, default=lambda self: self._ist_date(), tracking=True)
    daily_sheet_ref = fields.Integer(string="Daily Sheet Reference", tracking=True)
    total_amount = fields.Integer(string="Total Therapy Charges", compute="_compute_totals", store=True, tracking=True)
    therapy_amount = fields.Integer(string="Therapy Amount", tracking=True)
    first_cons_charges = fields.Integer(string="First Consultation Charges", tracking=True)
    therapy_medicine = fields.Integer(string="Therapy + Medicine", tracking=True)
    total_sessions = fields.Integer(string="Number of Therapy Sessions for which Patient has Enrolled", compute="_compute_totals", store=True, tracking=True)
    remaining_sessions = fields.Integer(string="Remaining Sessions", compute='_compute_remaining_sessions')
    used_sessions = fields.Integer(string="Number of Therapy Sessions Patient has already Completed", required=True, default=0, compute="_compute_totals", store=True, tracking=True)
    notes = fields.Char(string="Notes", tracking=True)
    enrollment_type = fields.Selection([
        ('clinic', 'Clinic'),
        ('home', 'Home'),
        ('self', 'Self'),
    ], string="Enrollment Type", required=True, tracking=True)
    state = fields.Selection([
        ('active', 'Active'),
        ('completed', 'Completed'),
    ], string="Status", default='active', tracking=True)
    pain_knee = fields.Boolean(string="Knee Pain")
    pain_spine = fields.Boolean(string="Spine Pain")
    enrolled_for = fields.Char(string="Enrolled For", compute="_compute_enrolled_for", store=True, tracking=True)
    line_ids = fields.One2many(
        'patient.enrollment.line',
        'enrollment_id',
        string="Enrollment Services"
    )
    payment_state = fields.Selection([
        ('draft', 'Draft'),
        ('bill_created', 'Bill Created'),
        ('paid', 'Paid'),
    ], default='draft', tracking=True)
    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True,
        related="patient_id.clinic_id"
    )
    payment_date = fields.Date()

    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order"
    )
    active = fields.Boolean(default=True)

    @api.depends(
        'line_ids.total_amount',
        'line_ids.total_sessions',
        'line_ids.used_sessions'
    )
    def _compute_totals(self):

        for rec in self:
            rec.total_amount = sum(
                rec.line_ids.mapped('total_amount')
            )

            rec.total_sessions = sum(
                rec.line_ids.mapped('total_sessions')
            )

            rec.used_sessions = sum(
                rec.line_ids.mapped('used_sessions')
            )

    @api.depends('total_sessions', 'used_sessions')
    def _compute_remaining_sessions(self):
        for rec in self:
            new_remaining = rec.total_sessions - rec.used_sessions
            # Only update if value actually changed
            if rec.remaining_sessions != new_remaining:
                rec.remaining_sessions = new_remaining

            # Update state only if needed
            if new_remaining == 0 and rec.state != 'completed':
                rec.state = 'completed'
            elif new_remaining > 0 and rec.state != 'active':
                rec.state = 'active'

    @api.model
    def create(self, vals):

        rec = super().create(vals)

        rec._update_patient_status()

        if rec.patient_id:
            rec.patient_id._compute_active_enrollment_id()

        return rec

    def _update_patient_status(self):

        for rec in self:

            if not rec.patient_id:
                continue

            patient = rec.patient_id

            # 1. UNPAID
            if rec.payment_state != 'paid':

                if patient.patient_status not in ['active', 'inactive']:
                    patient.patient_status = 'visit'

                continue

            product_names = rec.line_ids.mapped(
                'service_product_id.name'
            )

            therapy_products = [
                'Regeneration Therapy',
                'Complementary Therapy',
                'Self Therapy',
            ]

            # 2. ACTIVE THERAPY
            if any(product in product_names for product in therapy_products):

                # Therapy completed
                if rec.total_sessions > 0 and rec.total_sessions == rec.used_sessions:
                    patient.patient_status = 'inactive'

                else:
                    patient.patient_status = 'active'

            # 3. CONSULTATION ONLY
            elif 'Consultation Charges' in product_names:

                # Never downgrade active/inactive to visit
                if patient.patient_status not in ['active', 'inactive']:
                    patient.patient_status = 'visit'

            # 4. OTHER SERVICES
            else:
                patient.patient_status = 'inactive'

    def write(self, vals):

        if vals.get('active') is False:
            for rec in self:
                if rec.payment_state == 'paid':
                    raise UserError(_('You cannot archive an enrollment that has already been paid.'))
        # 1. Create a bypass for autonomous system updates
        # If Odoo is ONLY trying to update these specific fields, let it pass.
        allowed_system_fields = {'payment_state', 'state', 'active', 'used_sessions', 'remaining_sessions'}
        is_system_update = all(key in allowed_system_fields for key in vals.keys())

        for rec in self:
            # Only trigger the lock if a human/script is trying to edit a non-system field
            if not is_system_update:
                if (rec.total_sessions > 0 and rec.state == 'completed') or rec.payment_state == 'paid':
                    raise UserError(_('You cannot modify an enrollment that is already completed or paid.'))

        res = super(Enrollment, self).write(vals)

        for rec in self:

            if rec.patient_id:
                rec.patient_id._compute_active_enrollment_id()

            if vals.get('payment_state') == 'paid':
                rec._update_patient_status()

            if rec.patient_id:
                active_enrollments = self.search([
                    ('patient_id', '=', rec.patient_id.id),
                    ('state', '=', 'active'),
                    ('active', '=', True),
                ])

                # Safely downgrade to inactive if therapies run out
                if not active_enrollments and rec.patient_id.patient_status == 'active':
                    rec.patient_id.patient_status = 'inactive'

        return res

    @api.depends('pain_knee', 'pain_spine')
    def _compute_enrolled_for(self):
        for rec in self:
            selected = []
            if rec.pain_knee:
                selected.append("Knee Pain")
            if rec.pain_spine:
                selected.append("Spine Pain")
            rec.enrolled_for = ", ".join(selected)

    # @api.constrains('used_sessions', 'total_sessions')
    # def _check_used_sessions(self):
    #     for rec in self:
    #         if rec.used_sessions > rec.total_sessions:
    #             raise ValidationError(_("Used Sessions cannot be greater than Total Sessions."))
    #
    # @api.constrains('total_sessions')
    # def _check_total_sessions_zero(self):
    #     for rec in self:
    #         if rec.total_sessions == 0:
    #             raise ValidationError(_("Number of Therapy Sessions for which Patient has Enrolled Cannot be 0."))
    #
    #         elif rec.total_sessions > 100:
    #             raise ValidationError(_("You can enter a maximum of 100 therapy sessions."))
    #
    # @api.constrains('total_amount')
    # def _check_total_amount_zero(self):
    #     for rec in self:
    #         if rec.total_amount == 0:
    #             raise ValidationError(_("Total Therapy Charges Cannot be 0."))

    @api.constrains('enrollment_date')
    def _check_enrollment_date(self):
        today = date.today()
        for record in self:
            if record.enrollment_date and record.enrollment_date > today:
                raise ValidationError(
                    _("The enrollment date must be today or earlier.")
                )

    def action_open_bill_popup(self):

        self.ensure_one()

        enrollment = self.sudo().search([
            ('id', '=', self.id)
        ], limit=1)

        if enrollment.payment_state == 'paid':
            raise ValidationError(
                _("This enrollment is already paid.")
            )


        return {
            'name': 'Create Bill Confirmation',
            'type': 'ir.actions.act_window',
            'res_model': 'enrollment.bill.popup',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_enrollment_id': self.id,
                'default_total_amount': self.total_amount,
                'default_total_sessions': self.total_sessions,
            }
        }

    @api.model
    def get_pending_enrollments(self, clinic_id):
        records = self.search([
            ('clinic_id', '=', clinic_id),
            ('payment_state', '=', 'bill_created'),
            ('active', '=', True),
        ], order="enrollment_date desc")
        today = date.today()
        result = []
        for rec in records:
            result.append({
                "id": rec.id,
                "patient_id": rec.patient_id.partner_id.id if rec.patient_id.partner_id else False,
                "patient_name": rec.patient_id.name,
                "date": rec.enrollment_date.strftime('%d-%m-%Y') if rec.enrollment_date else "",
                "is_today": rec.enrollment_date == today,
                "bm_name": rec.doctor_id.name,
                "total_amount": rec.total_amount,
                "lines": [
                    {
                        "product_id": line.service_product_id.id,
                        "name": line.service_product_id.display_name,
                        "qty": line.pos_qty,
                        "amount": line.total_amount,
                        "unit_price": line.therapy_amount,
                    }
                    for line in rec.line_ids
                ],
            })
        return result

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        for record in self:
            if record.payment_state == 'paid':
                raise UserError(_("You cannot delete an enrollment that has already been paid."))
            record.active = False
        # Do not call super() → prevents actual deletion
        return True

class EnrollmentLine(models.Model):
    _name = 'patient.enrollment.line'
    _description = 'Patient Enrollment Line'

    enrollment_id = fields.Many2one(
        'patient.enrollment',
        string="Enrollment",
        required=True,
        ondelete='cascade'
    )

    service_product_id = fields.Many2one(
        'product.product',
        string="Service",
        domain="[('detailed_type','=','service')]",
        required=True
    )

    service_display_name = fields.Char(
        compute="_compute_service_display_name"
    )

    total_amount = fields.Integer(
        string="Total Amount",
        tracking=True
    )

    total_sessions = fields.Integer(
        string="Sessions",
        compute="_compute_total_sessions",
        store=True,
        tracking=True
    )

    used_sessions = fields.Integer(
        string="Used Sessions",
        tracking=True
    )

    therapy_amount = fields.Float(
        string="Per Session Amount",
        compute="_compute_therapy_amount",
        store=True
    )

    pos_qty = fields.Integer(
        string="Days",
        tracking=True
    )

    @api.onchange('service_product_id')
    def _onchange_service_product_id(self):

        self.total_amount = 0
        self.pos_qty = 0
        self.used_sessions = 0
        self.therapy_amount = 0

        if not self.service_product_id:
            return

        product_name = self.service_product_id.name

        # DEMO SESSION
        if product_name == 'Demo Session':

            self.pos_qty = 1

        # CONSULTATION
        elif product_name == 'Consultation Charges':

            self.pos_qty = 1

        # TREATMENTS
        elif product_name in [
            'Diabetes Treatment',
            'Digestion Improvement Treatment',
            'PCOD Treatment',
            'Regeneration Treatment',
            'Weight Management Treatment',
        ]:

            if not self.pos_qty:
                self.pos_qty = 1


    @api.depends('total_amount', 'pos_qty')
    def _compute_therapy_amount(self):

        for rec in self:

            if rec.pos_qty > 0:

                rec.therapy_amount = (
                        rec.total_amount / rec.pos_qty
                )

            else:

                rec.therapy_amount = 0

    @api.depends('service_product_id', 'pos_qty')
    def _compute_total_sessions(self):

        for rec in self:

            product_name = (
                rec.service_product_id.name
                if rec.service_product_id else ''
            )

            # SESSION BASED SERVICES
            if product_name in [
                'Demo Session',
                'Complementary Therapy',
                'Regeneration Therapy',
                'Self Therapy',
            ]:

                rec.total_sessions = rec.pos_qty

            # NON SESSION SERVICES
            else:

                rec.total_sessions = 0


    is_demo_session = fields.Boolean(compute="_compute_service_flags")
    is_complementary = fields.Boolean(compute="_compute_service_flags")
    is_consultation = fields.Boolean(compute="_compute_service_flags")
    is_diabetes_treatment = fields.Boolean(compute="_compute_service_flags")
    is_digestion_improvement = fields.Boolean(compute="_compute_service_flags")
    is_home_visit = fields.Boolean(compute="_compute_service_flags")
    is_pcod = fields.Boolean(compute="_compute_service_flags")
    is_regeneration_therapy = fields.Boolean(compute="_compute_service_flags")
    is_regeneration_treatment = fields.Boolean(compute="_compute_service_flags")
    is_self_therapy = fields.Boolean(compute="_compute_service_flags")
    is_weight_management_treatment = fields.Boolean(compute="_compute_service_flags")

    @api.depends('service_product_id')
    def _compute_service_flags(self):
        for rec in self:
            # Safely get the product name, default to empty string if not set
            product_name = rec.service_product_id.name if rec.service_product_id else ""

            rec.is_demo_session = (product_name == 'Demo Session')
            rec.is_complementary = (product_name == 'Complementary Therapy')
            rec.is_consultation = (product_name == 'Consultation Charges')
            rec.is_diabetes_treatment = (product_name == 'Diabetes Treatment')
            rec.is_digestion_improvement = (product_name == 'Digestion Improvement Treatment')
            rec.is_home_visit = (product_name == 'Home Visit Charges')
            rec.is_pcod = (product_name == 'PCOD Treatment')
            rec.is_regeneration_therapy = (product_name == 'Regeneration Therapy')
            rec.is_regeneration_treatment = (product_name == 'Regeneration Treatment')
            rec.is_self_therapy = (product_name == 'Self Therapy')
            rec.is_weight_management_treatment = (product_name == 'Weight Management Treatment')


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.depends('name')
    @api.depends_context('show_custom_regeneration_name')
    def _compute_display_name(self):
        # 1. Call standard behavior
        super()._compute_display_name()

        # 2. Check context (with cache bypassing enabled)
        if self.env.context.get('show_custom_regeneration_name'):
            for product in self:
                if product.name == 'Regeneration Therapy':
                    product.display_name = 'Regeneration Therapy(Consultation + Medicine + Therapy)'
                elif product.name == 'Regeneration Treatment':
                    product.display_name = 'Regeneration Treatment(Consultation + Medicine)'