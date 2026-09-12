from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


AUTO_LOCK_DAYS = 10


class InvoiceLockTemplate(models.Model):
    _name = 'invoice.lock.template'
    _description = 'Invoice Lock Company Template'
    _order = 'name'

    name = fields.Char(
        string="Template Name",
        required=True,
    )

    company_ids = fields.Many2many(
        'res.company',
        string="Companies",
        required=True,
    )

    _sql_constraints = [
        ('name_uniq', 'unique(name)',
         'A template with this name already exists.'),
    ]


class InvoiceLockWizard(models.TransientModel):
    _name = 'invoice.lock.wizard'
    _description = 'Invoice Lock Management'

    action_type = fields.Selection(
        [
            ('lock', 'Lock invoices on or before a date'),
            ('unlock', 'Unlock (remove all locks)'),
        ],
        string="Action",
        default='lock',
        required=True,
    )

    lock_date = fields.Date(
        string="Lock Date",
    )

    template_id = fields.Many2one(
        'invoice.lock.template',
        string="Template",
    )

    new_template_name = fields.Char(
        string="Save as",
    )

    company_ids = fields.Many2many(
        'res.company',
        string="Companies",
    )

    status_line_ids = fields.One2many(
        'invoice.lock.wizard.line',
        'wizard_id',
        string="Company Status",
    )

    audit_line_ids = fields.One2many(
        'invoice.lock.audit',
        compute='_compute_audit_line_ids',
        string="Activity Log",
    )

    auto_lock_cutoff = fields.Date(
        string="Auto-Lock Cutoff",
        compute='_compute_auto_lock_cutoff',
    )

    # -------------------------------------------------
    # Compute
    # -------------------------------------------------

    def _compute_auto_lock_cutoff(self):
        today = fields.Date.context_today(self)
        for wiz in self:
            wiz.auto_lock_cutoff = today - timedelta(days=AUTO_LOCK_DAYS)

    def _compute_audit_line_ids(self):
        Audit = self.env['invoice.lock.audit']
        for wiz in self:
            wiz.audit_line_ids = Audit.search([])

    # -------------------------------------------------
    # Default
    # -------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        today = fields.Date.context_today(self)
        auto_cutoff = today - timedelta(days=AUTO_LOCK_DAYS)

        companies = self.env['res.company'].search([])
        lines = []
        for c in companies:
            lines.append((0, 0, {
                'company_id': c.id,
                'manual_lock_date': c.invoice_manual_lock_date,
                'auto_lock_enabled': c.invoice_auto_lock_enabled,
                'auto_lock_cutoff': auto_cutoff if c.invoice_auto_lock_enabled else False,
            }))
        res['status_line_ids'] = lines
        return res

    # -------------------------------------------------
    # Onchange
    # -------------------------------------------------

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id:
            self.company_ids = [(6, 0, self.template_id.company_ids.ids)]
        else:
            self.company_ids = [(5, 0, 0)]

    # -------------------------------------------------
    # Template buttons
    # -------------------------------------------------

    def action_save_template(self):
        self.ensure_one()

        if not self.new_template_name:
            raise UserError("Please enter a template name.")
        if not self.company_ids:
            raise UserError("Please select at least one company.")

        existing = self.env['invoice.lock.template'].search([
            ('name', '=', self.new_template_name)
        ], limit=1)

        if existing:
            existing.write({'company_ids': [(6, 0, self.company_ids.ids)]})
            tpl = existing
        else:
            tpl = self.env['invoice.lock.template'].create({
                'name': self.new_template_name,
                'company_ids': [(6, 0, self.company_ids.ids)],
            })

        return self._reload(tpl_id=tpl.id)

    def action_delete_template(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError("Please select a template to delete.")
        self.template_id.unlink()
        return self._reload()

    def _reload(self, tpl_id=None):
        ctx = dict(self.env.context)
        if self.lock_date:
            ctx['default_lock_date'] = self.lock_date.isoformat()
        if self.action_type:
            ctx['default_action_type'] = self.action_type
        if tpl_id:
            ctx['default_template_id'] = tpl_id
            ctx['default_company_ids'] = [
                (6, 0, self.env['invoice.lock.template']
                 .browse(tpl_id).company_ids.ids)
            ]
        return {
            'type': 'ir.actions.act_window',
            'name': 'Invoice Lock Management',
            'res_model': 'invoice.lock.wizard',
            'view_mode': 'form',
            'target': 'current',
            'context': ctx,
        }

    # -------------------------------------------------
    # Apply action
    # -------------------------------------------------

    def action_apply(self):
        self.ensure_one()

        if not self.company_ids:
            raise UserError("Please select at least one company.")

        if self.action_type == 'lock':
            if not self.lock_date:
                raise UserError("Please choose a lock date.")

            today = fields.Date.context_today(self)
            if self.lock_date >= today:
                raise UserError(
                    f"Lock date must be in the past.\n"
                    f"You chose: {self.lock_date}\n"
                    f"Today: {today}"
                )

            self._perform_lock()

        elif self.action_type == 'unlock':
            self._perform_unlock()

        return self._reload(tpl_id=self.template_id.id or None)

    def _perform_lock(self):
        """Lock = set manual lock date + ensure auto-lock is ON."""
        Audit = self.env['invoice.lock.audit']
        for c in self.company_ids:
            previous = c.invoice_manual_lock_date
            c.write({
                'invoice_manual_lock_date': self.lock_date,
                'invoice_auto_lock_enabled': True,
            })
            Audit.create({
                'company_id': c.id,
                'action': 'lock',
                'previous_lock_date': previous,
                'new_lock_date': self.lock_date,
            })

    def _perform_unlock(self):
        """Unlock = clear manual lock + disable auto-lock entirely."""
        Audit = self.env['invoice.lock.audit']
        for c in self.company_ids:
            previous = c.invoice_manual_lock_date
            was_auto_on = c.invoice_auto_lock_enabled

            if previous or was_auto_on:
                c.write({
                    'invoice_manual_lock_date': False,
                    'invoice_auto_lock_enabled': False,
                })
                Audit.create({
                    'company_id': c.id,
                    'action': 'unlock',
                    'previous_lock_date': previous,
                    'new_lock_date': False,
                })


class InvoiceLockWizardLine(models.TransientModel):
    _name = 'invoice.lock.wizard.line'
    _description = 'Invoice Lock Status Line'

    wizard_id = fields.Many2one(
        'invoice.lock.wizard',
        required=True,
        ondelete='cascade',
    )

    company_id = fields.Many2one(
        'res.company',
        string="Company",
        required=True,
        readonly=True,
    )

    manual_lock_date = fields.Date(
        string="Manual Lock",
        readonly=True,
    )

    auto_lock_enabled = fields.Boolean(
        string="Auto-Lock On",
        readonly=True,
    )

    auto_lock_cutoff = fields.Date(
        string="Auto-Lock Cutoff",
        readonly=True,
    )

    effective_lock_date = fields.Date(
        string="Effective Lock",
        compute='_compute_effective_lock_date',
    )

    @api.depends('manual_lock_date', 'auto_lock_cutoff', 'auto_lock_enabled')
    def _compute_effective_lock_date(self):
        for rec in self:
            manual = rec.manual_lock_date
            auto = rec.auto_lock_cutoff if rec.auto_lock_enabled else False

            if manual and auto:
                rec.effective_lock_date = max(manual, auto)
            else:
                rec.effective_lock_date = manual or auto