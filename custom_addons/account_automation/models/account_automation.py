from odoo import models, fields


class AccountAutomationDashboard(models.TransientModel):
    _name = 'account.automation.dashboard'
    _description = 'Accountautomation dashboard'

    display_name = fields.Char(string='Display Name', compute='_compute_display_name')

    def _compute_display_name(self):
        for record in self:
            record.display_name = "Account Automation Workspace"

    def action_view_history(self):
        # Bypasses normal form target context to completely wipe technical IDs from the breadcrumb stack
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

    def action_run_bank_vs_hub_audit(self):
        """Triggers the new Bank vs HUB Collection execution wizard modal"""
        return {
            'name': 'BANK VS HUB Collection Audit',
            'type': 'ir.actions.act_window',
            'res_model': 'bank.sales.audit.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_run_bank_hub_audit(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bank.sales.audit.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_view_bank_hub_history(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'bank.sales.audit',
            'view_mode': 'tree,form',
            'target': 'current',
        }

    def action_run_clinic_performance(self):
        """Opens the date-selection wizard for the Clinic Performance Report."""
        return {
            'name': 'Clinic Performance Report',
            'type': 'ir.actions.act_window',
            'res_model': 'clinic.performance.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    def action_view_clinic_performance_history(self):
        """Opens the saved history of past Clinic Performance Report runs.

        Reuses the History action so the breadcrumb / search filters stay
        consistent with the menu entry under Operations.
        """
        action = self.env.ref(
            'account_automation.action_clinic_performance_report_history'
        ).read()[0]
        action.update({
            'target': 'current',
            'context': {'search_default_active': 1, 'clear_breadcrumbs': True},
        })
        return action
