from odoo import models, fields


class BankSalesAudit(models.Model):
    _name = 'bank.sales.audit'
    _description = 'Bank vs HUB Audit'
    _order = 'create_date desc'

    name = fields.Char(required=True)

    bank_config_id = fields.Many2one(
        'bank.statement.parser.config',
        string='Bank',
        required=True
    )

    start_date = fields.Date(required=True)
    end_date = fields.Date(required=True)

    file_name = fields.Char()

    run_by = fields.Many2one(
        'res.users',
        string='Run By',
        default=lambda self: self.env.user,
        readonly=True
    )

    run_date = fields.Datetime(
        string='Run On',
        default=fields.Datetime.now,
        readonly=True
    )

    line_ids = fields.One2many(
        'bank.sales.audit.line',
        'audit_id',
        string='Audit Lines'
    )


class BankSalesAuditLine(models.Model):
    _name = 'bank.sales.audit.line'
    _description = 'Bank vs HUB Audit Line'
    _order = 'difference desc, tid_number asc'

    audit_id = fields.Many2one(
        'bank.sales.audit',
        required=True,
        ondelete='cascade'
    )

    tid_number = fields.Char(string='TID')
    clinics_display = fields.Char(string='Clinic(s)')
    system_mode = fields.Char(string='Payment Mode')

    hub_amount = fields.Float(string='HUB Sales', digits=(16, 2))
    bank_amount = fields.Float(string='Bank Receipt', digits=(16, 2))
    difference = fields.Float(string='Variance', digits=(16, 2))
