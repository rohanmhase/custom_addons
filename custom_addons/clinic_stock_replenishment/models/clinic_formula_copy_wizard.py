from odoo import models, fields, api
from odoo.exceptions import UserError


class ClinicFormulaCopyWizard(models.TransientModel):
    _name = 'clinic.formula.copy.wizard'
    _description = 'Copy Formulas From Clinic'

    source_clinic_id = fields.Many2one(
        'stock.warehouse',
        string="Copy From Clinic",
        required=True
    )
    target_clinic_id = fields.Many2one(
        'stock.warehouse',
        string="Copy To Clinic",
        required=True
    )
    formula_count = fields.Integer(
        string="Formulas to Copy",
        compute="_compute_formula_count"
    )
    skip_count = fields.Integer(
        string="Already Existing (will skip)",
        compute="_compute_formula_count"
    )

    @api.depends('source_clinic_id', 'target_clinic_id')
    def _compute_formula_count(self):
        for rec in self:
            if not rec.source_clinic_id or not rec.target_clinic_id:
                rec.formula_count = 0
                rec.skip_count = 0
                continue
            source_formulas = self.env['stock.count.formula'].search([
                ('clinic_id', '=', rec.source_clinic_id.id)
            ])
            existing_products = self.env['stock.count.formula'].search([
                ('clinic_id', '=', rec.target_clinic_id.id)
            ]).mapped('product_id.id')
            to_copy = source_formulas.filtered(
                lambda r: r.product_id.id not in existing_products
            )
            rec.formula_count = len(to_copy)
            rec.skip_count = len(source_formulas) - len(to_copy)

    def action_confirm_copy(self):
        self.ensure_one()
        if self.source_clinic_id == self.target_clinic_id:
            raise UserError("Source and target clinic cannot be the same.")

        source_formulas = self.env['stock.count.formula'].search([
            ('clinic_id', '=', self.source_clinic_id.id)
        ])
        existing_products = self.env['stock.count.formula'].search([
            ('clinic_id', '=', self.target_clinic_id.id)
        ]).mapped('product_id.id')

        FORMULA_FIELDS = [
            'multiplier', 'starting_round_up', 'weekend_factor',
            'buffer', 'ending_round_up', 'minimum_value',
            'maximum_value', 'fixed_value', 'gender_filter',
        ]

        vals_list = []
        for formula in source_formulas:
            if formula.product_id.id in existing_products:
                continue
            vals = {'clinic_id': self.target_clinic_id.id,
                    'product_id': formula.product_id.id}
            for f in FORMULA_FIELDS:
                vals[f] = formula[f]
            vals_list.append(vals)

        self.env['stock.count.formula'].create(vals_list)
        return {'type': 'ir.actions.act_window_close'}

class ClinicFormulaApplyWizard(models.TransientModel):
    _name = 'clinic.formula.apply.wizard'
    _description = 'Apply Formula to All Clinics'

    source_formula_id = fields.Many2one(
        'stock.count.formula',
        string="Formula",
        readonly=True
    )
    product_id = fields.Many2one(
        related='source_formula_id.product_id',
        string="Product",
        readonly=True
    )
    clinic_count = fields.Integer(
        string="Clinics to Update",
        compute="_compute_clinic_count"
    )

    @api.depends('source_formula_id')
    def _compute_clinic_count(self):
        for rec in self:
            if not rec.source_formula_id:
                rec.clinic_count = 0
                continue
            rec.clinic_count = self.env['stock.count.formula'].search_count([
                ('product_id', '=', rec.source_formula_id.product_id.id),
                ('id', '!=', rec.source_formula_id.id),
            ])

    def action_confirm_apply(self):
        self.ensure_one()
        FORMULA_FIELDS = [
            'multiplier', 'starting_round_up', 'weekend_factor',
            'buffer', 'ending_round_up', 'minimum_value',
            'maximum_value', 'fixed_value', 'gender_filter',
        ]
        other_formulas = self.env['stock.count.formula'].search([
            ('product_id', '=', self.source_formula_id.product_id.id),
            ('id', '!=', self.source_formula_id.id),
        ])
        vals = {f: self.source_formula_id[f] for f in FORMULA_FIELDS}
        other_formulas.write(vals)
        return {'type': 'ir.actions.act_window_close'}