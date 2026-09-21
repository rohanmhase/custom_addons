from odoo import models


class ImportInvoicesWizardNatureInherit(models.TransientModel):
    """
    Lightweight inherit — only loaded if the CSV import module is installed.
    Injects a context flag so res.partner.create() auto-fills nature.
    Does NOT modify any field or logic in the wizard.
    """
    _inherit = 'import.invoices.wizard'

    def action_import(self):
        return super(
            ImportInvoicesWizardNatureInherit,
            self.with_context(from_csv_invoice_import=True),
        ).action_import()