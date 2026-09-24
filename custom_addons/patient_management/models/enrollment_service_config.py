from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class EnrollmentServiceConfig(models.Model):
    _name = 'patient.enrollment.service.config'
    _description = 'Enrollment Service Configuration'
    _order = 'sequence, product_id'

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    product_id = fields.Many2one(
        'product.product',
        string='Service Product',
        required=True,
        ondelete='cascade',
        index=True,
        domain="[('detailed_type', '=', 'service'), ('available_in_pos', '=', True)]",
    )

    service_category = fields.Selection([
        ('therapy', 'Therapy'),
        ('medicine', 'Medicine / Treatment'),
        ('consultation', 'Consultation'),
        ('other', 'Other'),
    ], string='Service Category', required=True, default='other')

    counts_as_sessions = fields.Boolean(
        string='Count Quantity as Sessions',
        help='When enabled, the enrollment line quantity contributes to total therapy sessions.'
    )

    quantity_mode = fields.Selection([
        ('hidden', 'Hidden'),
        ('fixed', 'Fixed / Read-only'),
        ('editable', 'Editable'),
    ], string='Quantity Behavior', required=True, default='editable')

    default_quantity = fields.Integer(
        string='Default Quantity',
        default=1,
        help='Quantity populated when the service is selected.'
    )

    require_positive_quantity = fields.Boolean(
        string='Require Quantity > 0',
        default=True,
    )

    show_total_amount = fields.Boolean(
        string='Show Total Amount',
        default=True,
    )

    show_unit_amount = fields.Boolean(
        string='Show Per Unit Amount',
        default=True,
    )

    activity_window_days = fields.Integer(
        string='Active Window (Days)',
        default=0,
        help=(
            'Used for patient status. Therapy services use this window from the latest '
            'therapy/enrollment date; medicine services use it from the enrollment date. '
            'Leave 0 for categories that do not control patient status.'
        ),
    )

    display_name_override = fields.Char(
        string='Enrollment Display Name',
        help='Optional display name used when enrollment services are displayed.'
    )

    _sql_constraints = [
        (
            'unique_product_enrollment_service_config',
            'unique(product_id)',
            'An enrollment service configuration already exists for this product.'
        ),
    ]

    @api.constrains('default_quantity', 'activity_window_days')
    def _check_non_negative_values(self):
        for rec in self:
            if rec.default_quantity < 0:
                raise ValidationError(_('Default Quantity cannot be negative.'))
            if rec.activity_window_days < 0:
                raise ValidationError(_('Active Window (Days) cannot be negative.'))

    @api.constrains('service_category', 'activity_window_days')
    def _check_status_window(self):
        for rec in self:
            if rec.service_category in ('therapy', 'medicine') and rec.activity_window_days <= 0:
                raise ValidationError(_(
                    'Therapy and Medicine / Treatment services must have an Active Window greater than 0 days.'
                ))

    @api.model
    def get_config_for_product(self, product):
        if not product:
            return self.browse()
        return self.with_context(active_test=False).search([
            ('product_id', '=', product.id)
        ], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Do not touch historical enrollment lines here.  Service configuration
        # saves must stay lightweight even when the database contains a large
        # enrollment history.  Historical links are backfilled separately.
        records.mapped('product_id').invalidate_recordset(['display_name'])
        return records

    def write(self, vals):
        # A configuration must stay attached to the same product once that
        # product has ever been used in an enrollment.  Check by product rather
        # than service_config_id because historical lines may not have been
        # backfilled yet.
        if 'product_id' in vals:
            EnrollmentLine = self.env['patient.enrollment.line']
            for rec in self:
                if rec.product_id and EnrollmentLine.search_count([
                    ('service_product_id', '=', rec.product_id.id)
                ]):
                    raise ValidationError(_(
                        'You cannot change the Service Product after this product has been used in an enrollment. '
                        'Archive this configuration and create a new one if needed.'
                    ))

        old_products = self.mapped('product_id')
        res = super().write(vals)

        # Configuration changes apply prospectively.  Do not recompute historical
        # enrollment lines/totals/statuses when an administrator saves this form.
        (old_products | self.mapped('product_id')).invalidate_recordset(['display_name'])
        return res

    def unlink(self):
        EnrollmentLine = self.env['patient.enrollment.line']
        for rec in self:
            if rec.product_id and EnrollmentLine.search_count([
                ('service_product_id', '=', rec.product_id.id)
            ]):
                raise ValidationError(_(
                    'You cannot delete an Enrollment Service Configuration whose product has already been used. '
                    'Archive it instead so historical enrollments remain correct.'
                ))

        products = self.mapped('product_id')
        res = super().unlink()
        products.invalidate_recordset(['display_name'])
        return res
