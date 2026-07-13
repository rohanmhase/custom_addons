from odoo import models, fields, api
from odoo.exceptions import UserError
from math import ceil
from datetime import date, timedelta


class StockCountFormula(models.Model):
    _name = 'stock.count.formula'
    _description = 'Stock Count Formula'
    _order = 'clinic_id, product_id'
    _rec_name = 'display_name'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    _sql_constraints = [
        (
            'clinic_product_unique',
            'unique(clinic_id, product_id)',
            'Each clinic can only have one pricing rule per product.'
        )
    ]

    # -------------------------------------------------
    # Clinic + Product
    # -------------------------------------------------

    clinic_id = fields.Many2one(
        'stock.warehouse',
        string="Clinic",
        required=True,
        index=True,
        tracking=True
    )

    product_id = fields.Many2one(
        'product.product',
        string="Product",
        required=True,
        domain="[('type','=','product')]",
        index=True,
        tracking=True
    )

    display_name = fields.Char(
        compute="_compute_display_name",
        store=True
    )

    # active = fields.Boolean(default=True, tracking=True)

    active = fields.Boolean(default=True, tracking=True)

    gender_filter = fields.Selection([
        ('all', 'All'),
        ('male', 'Male only'),
        ('female', 'Female only'),
    ], default='all', tracking=True)

    def get_yesterday_therapy_count(self):
        self.ensure_one()

        today = date.today()
        daily_counts = []
        for i in range(1, 4):
            count = self.env['patient.session'].search_count([
                ('session_date', '=', today - timedelta(days=i)),
                ('therapy_clinic_id.warehouse_id', '=', self.clinic_id.id),
            ])
            daily_counts.append(count)

        max_index = daily_counts.index(max(daily_counts))
        max_day_date = today - timedelta(days=(max_index + 1))

        total_on_max = daily_counts[max_index]

        if self.gender_filter == 'male':
            formula_count = self.env['patient.session'].search_count([
                ('session_date', '=', max_day_date),
                ('therapy_clinic_id.warehouse_id', '=', self.clinic_id.id),
                ('patient_id.gender', '=', 'male'),
            ])
        elif self.gender_filter == 'female':
            formula_count = self.env['patient.session'].search_count([
                ('session_date', '=', max_day_date),
                ('therapy_clinic_id.warehouse_id', '=', self.clinic_id.id),
                ('patient_id.gender', '=', 'female'),
            ])
        else:
            formula_count = total_on_max

        return daily_counts, max_index, formula_count

        # therapy_count = max(daily_counts)
        # print(f"DEBUG → Max therapy count selected: {therapy_count}")
        # return therapy_count

    # def calculate_from_yesterday(self):
    #     self.ensure_one()
    #
    #     therapy_count = self.get_yesterday_therapy_count()
    #
    #     return self.calculate_price(therapy_count)

    def calculate_from_yesterday(self):
        self.ensure_one()

        therapy_data, _, formula_count = self.get_yesterday_therapy_count()

        return self.calculate_price(formula_count)

    def action_apply_to_all_clinics(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Apply to All Clinics',
            'res_model': 'clinic.formula.apply.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_source_formula_id': self.id},
        }

    def action_open_copy_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Copy Clinic Formulas',
            'res_model': 'clinic.formula.copy.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    # -------------------------------------------------
    # Calculation Fields
    # -------------------------------------------------

    multiplier = fields.Float(
        tracking=True
    )

    starting_round_up = fields.Boolean(
        string="Round Up After Multiplier",
        tracking=True
    )

    weekend_factor = fields.Float(
        tracking=True
    )

    buffer = fields.Float(
        tracking=True
    )

    ending_round_up = fields.Boolean(
        string="Round Up Final Result",
        tracking=True
    )

    minimum_value = fields.Float(tracking=True)
    maximum_value = fields.Float(tracking=True)

    fixed_value = fields.Float(
        help="If set, overrides all other logic",
        tracking=True
    )

    # -------------------------------------------------
    # Preview Fields
    # -------------------------------------------------

    preview_therapy_count = fields.Integer(
        default=20,
        string="Preview Therapy Count"
    )

    preview_base = fields.Float(compute="_compute_preview")
    preview_after_start_round = fields.Float(compute="_compute_preview")
    preview_after_weekend = fields.Float(compute="_compute_preview")
    preview_after_buffer = fields.Float(compute="_compute_preview")
    preview_after_end_round = fields.Float(compute="_compute_preview")
    preview_final = fields.Float(compute="_compute_preview")

    # -------------------------------------------------
    # ARCHIVE INSTEAD OF DELETE
    # -------------------------------------------------

    def unlink(self):
        # If record is active, archive it instead (send to recycle bin)
        active_records = self.filtered('active')
        if active_records:
            active_records.write({'active': False})
            return True

        # If record is already archived, hard delete it
        inactive_records = self.filtered(lambda r: not r.active)
        if inactive_records:
            return super(StockCountFormula, inactive_records).unlink()

        return True

    # -------------------------------------------------
    # Display Name
    # -------------------------------------------------

    @api.depends('clinic_id', 'product_id')
    def _compute_display_name(self):
        for record in self:
            if record.clinic_id and record.product_id:
                record.display_name = f"{record.clinic_id.name} - {record.product_id.display_name}"
            else:
                record.display_name = "New Rule"

    # -------------------------------------------------
    # Core Calculation Engine
    # Empty field = skip that phase
    # -------------------------------------------------

    def calculate_price(self, therapy_count, reference_date=None):
        self.ensure_one()

        if self.fixed_value:
            return self.fixed_value

        # If all fields are 0/False, no formula is configured → return 0
        has_any_config = any([
            self.multiplier,
            self.starting_round_up,
            self.weekend_factor,
            self.buffer,
            self.ending_round_up,
            self.minimum_value,
            self.maximum_value,
        ])
        if not has_any_config:
            return 0.0

        result = float(therapy_count)

        if self.multiplier:
            result = therapy_count * self.multiplier

        if self.starting_round_up:
            result = ceil(result)
        if self.weekend_factor:
            check_date = reference_date or date.today()
            is_weekend = check_date.weekday() in (4, 5)  # 5=Saturday, 4=Friday
            if is_weekend:
                result = result * self.weekend_factor
        if self.buffer:
            result = result + self.buffer

        if self.ending_round_up:
            result = ceil(result)

        if self.minimum_value:
            result = max(result, self.minimum_value)

        if self.maximum_value:
            result = min(result, self.maximum_value)

        return result

    # -------------------------------------------------
    # Live Preview
    # -------------------------------------------------

    @api.depends(
        'multiplier',
        'starting_round_up',
        'weekend_factor',
        'buffer',
        'ending_round_up',
        'minimum_value',
        'maximum_value',
        'fixed_value',
        'preview_therapy_count'
    )
    def _compute_preview(self):
        for record in self:

            tc = record.preview_therapy_count or 0

            if record.fixed_value:
                record.preview_base = 0
                record.preview_after_start_round = 0
                record.preview_after_weekend = 0
                record.preview_after_buffer = 0
                record.preview_after_end_round = 0
                record.preview_final = record.fixed_value
                continue

            base = (tc * record.multiplier) if record.multiplier else float(tc)
            record.preview_base = base

            after_start = ceil(base) if record.starting_round_up else base
            record.preview_after_start_round = after_start

            after_weekend = (after_start * record.weekend_factor) if record.weekend_factor else after_start
            record.preview_after_weekend = after_weekend

            after_buffer = (after_weekend + record.buffer) if record.buffer else after_weekend
            record.preview_after_buffer = after_buffer

            after_end = ceil(after_buffer) if record.ending_round_up else after_buffer
            record.preview_after_end_round = after_end

            final = after_end

            if record.minimum_value:
                final = max(final, record.minimum_value)

            if record.maximum_value:
                final = min(final, record.maximum_value)

            record.preview_final = final

    # -------------------------------------------------
    # Validation
    # -------------------------------------------------

    @api.constrains('minimum_value', 'maximum_value')
    def _check_min_max(self):
        for record in self:
            if (
                    record.minimum_value
                    and record.maximum_value
                    and record.minimum_value > record.maximum_value
            ):
                raise UserError("Minimum value cannot be greater than maximum value.")