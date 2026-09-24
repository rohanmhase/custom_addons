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

    def _recompute_lines_for_products(self, products):
        if not products:
            return

        # This operation intentionally refreshes historical enrollment lines so
        # existing services receive their configuration.  The context flag tells
        # patient.enrollment.write() that any resulting stored-compute writes are
        # internal recomputations, not an attempt by a user to edit a paid record.
        lines = self.env['patient.enrollment.line'].with_context(
            service_config_recompute=True
        ).search([
            ('service_product_id', 'in', products.ids)
        ])
        if lines:
            lines._compute_service_config_id()
            lines._compute_total_sessions()

            enrollments = lines.mapped('enrollment_id').with_context(
                service_config_recompute=True
            )
            if enrollments:
                enrollments._compute_totals()
                enrollments._update_patient_status()

        products.invalidate_recordset(['display_name'])

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get('skip_service_recompute'):
            records._recompute_lines_for_products(records.mapped('product_id'))
        return records

    def write(self, vals):
        if 'product_id' in vals:
            linked_lines = self.env['patient.enrollment.line'].search_count([
                ('service_config_id', 'in', self.ids)
            ])
            if linked_lines:
                raise ValidationError(_(
                    'You cannot change the Service Product after this configuration has been used in an enrollment. '
                    'Archive this configuration and create a new one if needed.'
                ))

        old_products = self.mapped('product_id')
        res = super().write(vals)
        if not self.env.context.get('skip_service_recompute'):
            self._recompute_lines_for_products(old_products | self.mapped('product_id'))
        return res

    def unlink(self):
        linked_lines = self.env['patient.enrollment.line'].search_count([
            ('service_config_id', 'in', self.ids)
        ])
        if linked_lines:
            raise ValidationError(_(
                'You cannot delete an Enrollment Service Configuration that has already been used. '
                'Archive it instead so historical enrollments remain correct.'
            ))

        products = self.mapped('product_id')
        res = super().unlink()
        products.invalidate_recordset(['display_name'])
        return res
