from odoo import models, fields, api, _
from datetime import datetime, timedelta, date
from odoo.exceptions import UserError, ValidationError


class Enrollment(models.Model):
    _name = 'patient.enrollment'
    _description = 'Patient Enrollment'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    patient_id = fields.Many2one('clinic.patient', string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one('res.users', string="BM", required=True, readonly=True, default=lambda self: self.env.user)
    enrollment_date = fields.Date(string="Enrollment Date", required=True, default=lambda self: self._ist_date(), tracking=True, index=True)
    daily_sheet_ref = fields.Integer(string="Daily Sheet Reference", tracking=True)
    total_amount = fields.Integer(string="Total Amount", compute="_compute_totals", store=True, tracking=True)
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
    pain_knee = fields.Boolean(string="Knee Therapy")
    pain_spine = fields.Boolean(string="Spine Therapy")
    pain_other = fields.Boolean(string="Other Therapy")
    pain_multi = fields.Boolean(string="Multi-Joint Therapy")
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
    ], default='draft', tracking=True, index=True)
    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True,
        related="patient_id.clinic_id",
        index=True
    )
    payment_date = fields.Date()

    pos_order_id = fields.Many2one(
        "pos.order",
        string="POS Order"
    )
    active = fields.Boolean(default=True, index=True)

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
            status_needs_update = False
            # Only update if value actually changed
            if rec.remaining_sessions != new_remaining:
                rec.remaining_sessions = new_remaining
                status_needs_update = False

            # Update state only if needed
            if new_remaining == 0 and rec.state != 'completed':
                rec.state = 'completed'
                status_needs_update = False
            elif new_remaining > 0 and rec.state != 'active':
                rec.state = 'active'
                status_needs_update = False

            if status_needs_update and rec.id:
                rec._update_patient_status()

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

            paid_enrollments = self.env['patient.enrollment'].search([
                ('patient_id', '=', patient.id),
                ('payment_state', '=', 'paid'),
                ('active', '=', True)
            ], order='enrollment_date desc, id desc')

            if not paid_enrollments:
                if patient.patient_status not in ['active', 'on_medicine', 'inactive']:
                    patient.patient_status = 'visit'
                continue

            has_active_therapy = False
            has_active_medicine = False
            today = fields.Date.today()

            last_session = self.env['patient.session'].search([
                ('patient_id', '=', patient.id),
                ('active', '=', True)
            ], order='session_date desc', limit=1)
            last_session_date = last_session.session_date if last_session else False

            for enr in paid_enrollments:
                therapy_lines = enr.line_ids.filtered(
                    lambda line: line.service_config_id
                    and line.service_config_id.service_category == 'therapy'
                )
                medicine_lines = enr.line_ids.filtered(
                    lambda line: line.service_config_id
                    and line.service_config_id.service_category == 'medicine'
                )

                if therapy_lines and enr.total_sessions > enr.used_sessions:
                    active_date = last_session_date or enr.enrollment_date
                    if active_date:
                        therapy_window = max(
                            therapy_lines.mapped('service_config_id.activity_window_days') or [0]
                        )
                        diff_days = (today - active_date).days
                        if therapy_window > 0 and diff_days <= therapy_window:
                            has_active_therapy = True

                if medicine_lines and enr.enrollment_date:
                    medicine_window = max(
                        medicine_lines.mapped('service_config_id.activity_window_days') or [0]
                    )
                    diff_days = (today - enr.enrollment_date).days
                    if medicine_window > 0 and diff_days < medicine_window:
                        has_active_medicine = True

            if has_active_therapy:
                patient.patient_status = 'active'
            elif has_active_medicine:
                patient.patient_status = 'on_medicine'
            else:
                latest_enr = paid_enrollments[0]
                latest_categories = set(
                    latest_enr.line_ids.filtered('service_config_id').mapped(
                        'service_config_id.service_category'
                    )
                )

                has_consultation = 'consultation' in latest_categories
                has_major_service = bool({'therapy', 'medicine'} & latest_categories)

                if has_consultation and not has_major_service:
                    past_major_enrollments = paid_enrollments.filtered(
                        lambda enrollment: any(
                            line.service_config_id
                            and line.service_config_id.service_category in ('therapy', 'medicine')
                            for line in enrollment.line_ids
                        )
                    )

                    if past_major_enrollments:
                        patient.patient_status = 'inactive'
                    else:
                        patient.patient_status = 'visit'
                else:
                    patient.patient_status = 'inactive'

    def write(self, vals):

        if vals.get('active') is False:
            for rec in self:
                if rec.payment_state == 'paid':
                    raise UserError(_('You cannot archive an enrollment that has already been paid.'))
        # 1. Create a bypass for autonomous system updates
        # If Odoo is ONLY trying to update these specific fields, let it pass.
        # Stored computed fields can be recomputed by Odoo when an Enrollment
        # Service Configuration is created/updated. Those are system updates and
        # must not be blocked by the paid/completed enrollment edit lock.
        allowed_system_fields = {
            'payment_state',
            'state',
            'active',
            'used_sessions',
            'remaining_sessions',
            'total_sessions',
            'total_amount',
        }
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

            if any(key in vals for key in ['payment_state', 'state', 'used_sessions', 'active']):
                rec._update_patient_status()

        return res

    @api.depends('pain_knee', 'pain_spine', 'pain_other', 'pain_multi')
    def _compute_enrolled_for(self):
        for rec in self:
            selected = []
            if rec.pain_knee:
                selected.append("Knee Therapy")
            if rec.pain_spine:
                selected.append("Spine Therapy")
            if rec.pain_other:
                selected.append("Other Therapy")
            if rec.pain_multi:
                selected.append("Multi-Joint Therapy")
            rec.enrolled_for = ", ".join(selected)

    # @api.constrains('used_sessions', 'total_sessions')
    # def _check_used_sessions(self):
    #     for rec in self:
    #         if rec.used_sessions > rec.total_sessions:
    #             raise ValidationError(_("Used Sessions cannot be greater than Total Sessions."))
    #
    @api.constrains('total_sessions')
    def _check_total_sessions_zero(self):
        for rec in self:
            if rec.total_sessions > 100:
                raise ValidationError(_("You can enter a maximum of 100 therapy sessions."))
    #
    # @api.constrains('total_amount')
    # def _check_total_amount_zero(self):
    #     for rec in self:
    #         if rec.total_amount == 0:
    #             raise ValidationError(_("Total Therapy Charges Cannot be 0."))

    @api.constrains('enrollment_date')
    def _check_enrollment_date(self):
        for record in self:
            today_ist = record._ist_date()
            if record.enrollment_date and record.enrollment_date > today_ist:
                raise ValidationError(
                    _("The enrollment date must be today or earlier.")
                )

    def action_mark_as_paid(self):
        self.ensure_one()

        if self.payment_state == 'paid':
            raise UserError(_("This enrollment is already marked as paid."))

        self.write({
            'payment_state': 'paid'
            # 'payment_date': fields.Date.today(),
        })

        self.message_post(
            body=_("Enrollment marked as paid by %s.") % self.env.user.name
        )

        return True

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
            billing_lines = []

            for line in rec.line_ids:
                mrp = line._get_fixed_medicine_package_mrp()

                if mrp:
                    # Both the configured MRP and the amount entered on the
                    # enrollment line are GROSS values (GST included).
                    #
                    # If the product tax is configured as price-excluded, POS
                    # expects a tax-exclusive unit price.  Convert the inclusive
                    # MRP to the correct POS base before applying the discount.
                    # If the tax is already price-included, this helper simply
                    # returns the MRP unchanged.
                    discounted_amount = float(line.total_amount or 0.0)
                    mrp = line._get_fixed_medicine_package_mrp()

                    discount_pct = 0.0
                    pos_unit_price = line._get_pos_unit_price_from_gross(mrp)

                    if mrp > 0 and discounted_amount < mrp:

                        # Invoice stores discount at 2 decimals.
                        # Calculate exactly the same discount that will reach account.move.line.
                        discount_pct = round(
                            ((mrp - discounted_amount) / mrp) * 100.0,
                            2,
                        )

                        discount_factor = 1.0 - (discount_pct / 100.0)

                        if discount_factor > 0:
                            # Because discount is rounded to 2 decimals on the invoice,
                            # compensate the unit price so the final GST-inclusive
                            # invoice total still equals the Enrollment Total Amount.
                            adjusted_gross_before_discount = (
                                    discounted_amount / discount_factor
                            )

                            pos_unit_price = (
                                line._get_pos_unit_price_from_gross(
                                    adjusted_gross_before_discount
                                )
                            )

                    billing_lines.append({
                        "product_id": line.service_product_id.id,
                        "name": line.service_product_id.display_name,
                        "qty": 1,
                        "amount": discounted_amount,
                        "unit_price": pos_unit_price,
                        "discount": discount_pct,
                        "mrp": mrp,
                        "gross_target": discounted_amount,
                        "is_fixed_medicine_package": True,
                    })
                else:
                    billing_lines.append({
                        "product_id": line.service_product_id.id,
                        "name": line.service_product_id.display_name,
                        "qty": line.pos_qty,
                        "amount": line.total_amount,
                        "unit_price": line.therapy_amount,
                        "discount": 0.0,
                        "mrp": 0.0,
                        "is_fixed_medicine_package": False,
                    })

            result.append({
                "id": rec.id,
                "patient_id": rec.patient_id.partner_id.id if rec.patient_id.partner_id else False,
                "patient_name": rec.patient_id.name,
                "date": rec.enrollment_date.strftime('%d-%m-%Y') if rec.enrollment_date else "",
                "is_today": rec.enrollment_date == today,
                "bm_name": rec.doctor_id.name,
                "total_amount": rec.total_amount,
                "lines": billing_lines,
            })

        return result

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))

    @api.constrains('line_ids')
    def _check_empty_lines(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_("You must add at least one enrollment service line before saving."))

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

    def action_archive(self):
        # 1. Check if the action was triggered from our locked-down dashboard
        if self.env.context.get('block_archive'):
            raise UserError("You cannot archive records directly from the Dashboard view.")

        # 2. Otherwise, allow normal archiving behavior
        return super().action_archive()

class EnrollmentLine(models.Model):
    _name = 'patient.enrollment.line'
    _description = 'Patient Enrollment Line'

    # These products use their Odoo Sales Price as the GST-inclusive MRP.
    # The MRP itself is NOT hard-coded: change the product Sales Price in Odoo
    # and new enrollment/POS calculations will automatically use the new value.
    MEDICINE_PACKAGE_PRODUCT_NAMES = {
        '90 Days Medicine Package',
        '180 Days Medicine Package',
        '270 Days Medicine Package',
        '365 Days Medicine Package',
    }

    def _is_fixed_medicine_package(self):
        self.ensure_one()
        return bool(
            self.service_product_id
            and self.service_product_id.name in self.MEDICINE_PACKAGE_PRODUCT_NAMES
        )

    def _get_fixed_medicine_package_mrp(self):
        """Return the medicine-package MRP INCLUDING customer taxes.

        The product Sales Price (``list_price``) is maintained from the Odoo
        product screen.  For these medicine packages, enrollment must use the
        customer-facing MRP including GST.  We therefore let Odoo's tax engine
        compute the tax-inclusive value from the product's Customer Taxes.

        This works for both tax configurations:
        * price-excluded tax: Sales Price 16,190.48 + 5% GST -> ~17,000 MRP
        * price-included tax: ``total_included`` stays equal to the entered price
        """
        self.ensure_one()
        if not self._is_fixed_medicine_package():
            return 0.0

        product = self.service_product_id
        base_price = float(product.list_price or 0.0)
        if base_price <= 0:
            raise ValidationError(_(
                'Please configure a Sales Price greater than 0 for %s before using it in enrollment.'
            ) % product.display_name)

        company = self.env.company
        taxes = product.taxes_id.filtered(lambda tax: tax.company_id == company)
        if not taxes:
            return base_price

        partner = self.enrollment_id.patient_id.partner_id
        tax_result = taxes.compute_all(
            base_price,
            currency=company.currency_id,
            quantity=1.0,
            product=product,
            partner=partner,
        )

        return float(tax_result.get('total_included', base_price) or base_price)

    def _get_pos_unit_price_from_gross(self, gross_amount):
        """Return the POS unit price that produces ``gross_amount`` after tax.

        Medicine package MRPs are business-defined GST-inclusive amounts.
        Odoo POS, however, expects ``price`` according to the product tax's
        ``price_include`` setup.  This method supports both configurations:

        * price-included tax  -> return the gross MRP unchanged
        * price-excluded tax  -> back GST out of the MRP first

        For the current 5% GST setup this turns Rs. 4,999 gross into about
        Rs. 4,760.95238 before tax.  Applying the enrollment discount to that
        base and then GST yields the exact discounted gross total.
        """
        self.ensure_one()

        gross_amount = float(gross_amount or 0.0)
        if gross_amount <= 0 or not self.service_product_id:
            return gross_amount

        product = self.service_product_id
        company = self.env.company
        taxes = product.taxes_id.filtered(
            lambda tax: tax.company_id == company
        )

        if not taxes:
            return gross_amount

        currency = company.currency_id
        partner = self.enrollment_id.patient_id.partner_id

        tax_result = taxes.compute_all(
            gross_amount,
            currency=currency,
            quantity=1.0,
            product=product,
            partner=partner,
        )

        computed_total = float(
            tax_result.get('total_included', gross_amount) or gross_amount
        )

        # If compute_all() adds tax on top of the supplied value, the tax is
        # price-excluded.  Scale the gross MRP down to the tax-exclusive base.
        tolerance = max(currency.rounding or 0.01, 0.000001)
        if computed_total > gross_amount + tolerance:
            factor = computed_total / gross_amount
            if factor > 0:
                return gross_amount / factor

        # Tax is already included in the product price.
        return gross_amount

    enrollment_id = fields.Many2one(
        'patient.enrollment',
        string="Enrollment",
        required=True,
        ondelete='cascade'
    )

    service_product_id = fields.Many2one(
        'product.product',
        string="Service",
        domain="[('detailed_type','=','service'), ('available_in_pos', '=', True)]",
        required=True,
        index=True,
    )

    service_config_id = fields.Many2one(
        'patient.enrollment.service.config',
        string='Service Configuration',
        compute='_compute_service_config_id',
        store=True,
        readonly=True,
        index=True,
    )

    service_category = fields.Selection(
        related='service_config_id.service_category',
        readonly=True,
    )
    quantity_mode = fields.Selection(
        related='service_config_id.quantity_mode',
        readonly=True,
    )
    show_total_amount = fields.Boolean(
        related='service_config_id.show_total_amount',
        readonly=True,
    )
    show_unit_amount = fields.Boolean(
        related='service_config_id.show_unit_amount',
        readonly=True,
    )
    require_positive_quantity = fields.Boolean(
        related='service_config_id.require_positive_quantity',
        readonly=True,
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

    @api.depends('service_product_id')
    def _compute_service_config_id(self):
        products = self.mapped('service_product_id')
        configs = self.env['patient.enrollment.service.config'].with_context(
            active_test=False
        ).search([
            ('product_id', 'in', products.ids)
        ]) if products else self.env['patient.enrollment.service.config']

        config_by_product = {config.product_id.id: config for config in configs}
        for rec in self:
            rec.service_config_id = config_by_product.get(rec.service_product_id.id)

    @api.onchange('service_product_id')
    def _onchange_service_product_id(self):
        self.total_amount = 0
        self.pos_qty = 0
        self.used_sessions = 0
        self.therapy_amount = 0

        if not self.service_product_id:
            self.service_config_id = False
            return

        config = self.env['patient.enrollment.service.config'].get_config_for_product(
            self.service_product_id
        )
        self.service_config_id = config

        if not config or not config.active:
            return {
                'warning': {
                    'title': _('Service Not Configured'),
                    'message': _(
                        'This service has no active Enrollment Service Configuration. '
                        'Please configure it before using it in an enrollment.'
                    ),
                }
            }

        self.pos_qty = config.default_quantity

        # Fixed medicine packages always start at their MRP and quantity 1.
        # The user can then reduce Total Amount to give a discount.
        medicine_mrp = self._get_fixed_medicine_package_mrp()
        if medicine_mrp:
            self.pos_qty = 1
            self.total_amount = int(medicine_mrp)

    @api.depends('total_amount', 'pos_qty')
    def _compute_therapy_amount(self):
        for rec in self:
            if rec.pos_qty > 0:
                rec.therapy_amount = rec.total_amount / rec.pos_qty
            else:
                rec.therapy_amount = 0

    @api.depends(
        'service_product_id',
        'service_config_id',
        'pos_qty'
    )
    def _compute_total_sessions(self):
        for rec in self:
            if rec.service_config_id and rec.service_config_id.counts_as_sessions:
                rec.total_sessions = rec.pos_qty
            else:
                rec.total_sessions = 0

    @api.constrains('service_product_id')
    def _check_service_configuration(self):
        Config = self.env['patient.enrollment.service.config']
        for rec in self:
            if not rec.service_product_id:
                continue

            config = Config.get_config_for_product(rec.service_product_id)
            if not config or not config.active:
                raise ValidationError(_(
                    'Service "%s" is not configured for enrollment. '
                    'Please create/activate its Enrollment Service Configuration first.'
                ) % rec.service_product_id.display_name)

    @api.constrains('pos_qty', 'service_product_id', 'service_config_id')
    def _check_treatment_qty(self):
        for rec in self:
            if (
                rec.service_product_id
                and rec.service_config_id
                and rec.service_config_id.require_positive_quantity
                and rec.pos_qty <= 0
            ):
                raise ValidationError(
                    _("The quantity (Days) for %s must be greater than 0.")
                    % rec.service_product_id.display_name
                )

    @api.constrains('service_product_id', 'pos_qty', 'total_amount')
    def _check_fixed_medicine_package(self):
        for rec in self:
            mrp = rec._get_fixed_medicine_package_mrp()
            if not mrp:
                continue

            if rec.pos_qty != 1:
                raise ValidationError(_(
                    '%s must always have quantity 1.'
                ) % rec.service_product_id.display_name)

            if rec.total_amount <= 0:
                raise ValidationError(_(
                    'The discounted amount for %s must be greater than 0.'
                ) % rec.service_product_id.display_name)

            if rec.total_amount > mrp:
                raise ValidationError(_(
                    '%s has a maximum MRP of Rs. %s. You may discount it, '
                    'but the amount cannot be higher than MRP.'
                ) % (rec.service_product_id.display_name, int(mrp)))

    # @api.constrains('therapy_amount')
    # def _check_therapy_amount_decimals(self):
    #     for rec in self:
    #         if rec.therapy_amount and not rec.therapy_amount.is_integer():
    #             raise ValidationError(
    #                 _(
    #                     "The Per Session Amount must be a whole number. "
    #                     "Decimal values like %s are not allowed."
    #                 ) % rec.therapy_amount
    #             )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    @api.depends('name')
    @api.depends_context('show_custom_regeneration_name')
    def _compute_display_name(self):
        super()._compute_display_name()

        if not self.env.context.get('show_custom_regeneration_name'):
            return

        configs = self.env['patient.enrollment.service.config'].with_context(
            active_test=False
        ).search([
            ('product_id', 'in', self.ids),
            ('display_name_override', '!=', False),
        ])
        override_by_product = {
            config.product_id.id: config.display_name_override
            for config in configs
            if config.display_name_override
        }

        for product in self:
            override = override_by_product.get(product.id)
            if override:
                product.display_name = override

