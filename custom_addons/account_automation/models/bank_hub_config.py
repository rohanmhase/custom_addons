from odoo import models, fields


class BankStatementParserConfig(models.Model):
    _name = 'bank.statement.parser.config'
    _description = 'Bank CSV Configuration'
    _rec_name = 'bank_name'

    bank_name = fields.Char(string='Bank Name', required=True)
    col_tid = fields.Char(string='TID Column Header', required=True)
    col_amount = fields.Char(string='Amount Column Header', required=True)
    col_mode = fields.Char(string='Pay Mode Column Header', required=True)

    mapping_ids = fields.One2many(
        'bank.statement.mode.mapping', 'config_id',
        string='Payment Mode Translations'
    )


class BankStatementModeMapping(models.Model):
    _name = 'bank.statement.mode.mapping'
    _description = 'Bank Raw Value to System Mode Mapping'

    config_id = fields.Many2one(
        'bank.statement.parser.config',
        ondelete='cascade'
    )

    raw_value = fields.Char(
        string='Bank Raw Value',
        required=True,
        help="Exact value that appears in the bank CSV payment mode column, e.g. 'UPI', 'CARD'"
    )

    label = fields.Char(
        string='Display Label',
        required=True,
        help="Label shown in audit results, e.g. 'ICICI UPI'. "
             "Both global and local methods map here so results stay in one row."
    )

    # Many2many: map one bank raw value to BOTH global + local POS payment methods.
    # Example: 'UPI' → ['ICICI UPI (global)', 'ICICI UPI VAR (local Varachha)']
    # This ensures every branch's local method is included in the HUB SQL pull.
    system_mode_ids = fields.Many2many(
        'pos.payment.method',
        'bank_mode_mapping_method_rel',
        'mapping_id',
        'method_id',
        string='POS Payment Methods (Global + Local)',
        required=True
    )


class TidClinicMethodMapping(models.Model):
    _name = 'tid.clinic.method.mapping'
    _description = 'TID vs Clinic Mapping'
    _rec_name = 'tid_number'

    bank_config_id = fields.Many2one(
        'bank.statement.parser.config',
        string='Bank Name',
        required=True
    )
    tid_number = fields.Char(string='Terminal ID (TID)', required=True)
    clinic_id = fields.Many2one(
        'pos.config',
        string='Clinic / POS Location',
        required=True
    )