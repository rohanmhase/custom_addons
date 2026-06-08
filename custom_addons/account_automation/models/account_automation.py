from odoo import models, fields, api


class AccountAutomationDashboard(models.TransientModel):
    _name = 'account.automation.dashboard'
    _description = 'Accountautomation dashboard'

    # Overrides standard naming to force a clean string in all background views/breadcrumbs
    display_name = fields.Char(string='Display Name', compute='_compute_display_name')

    def _compute_display_name(self):
        for record in self:
            record.display_name = "Account Automation Workspace"

    def action_view_history(self):
        # Fetch the regular menu action directly to break the breadcrumb inheritance chain
        action = self.env.ref('account_automation.action_daily_sales_comparison').read()[0]
        action.update({
            'target': 'current',
            'context': {'clear_breadcrumbs': True}
        })
        return action

    def action_run_audit(self):
        return {
            'name': 'Daily Sales Comparison',
            'type': 'ir.actions.act_window',
            'res_model': 'psmr.reconciliation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }