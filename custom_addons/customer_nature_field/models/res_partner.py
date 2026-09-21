import inspect
from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    nature_id = fields.Many2one(
        'res.partner.nature',
        string='Nature',
        help='Classification of this customer (e.g., Clinic Sales, Online Sales)',
        ondelete='set null',
        index=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        is_clinic = False
        is_online = False

        # Detect source without touching other modules
        frame = inspect.currentframe()
        try:
            curr = frame.f_back
            for _ in range(12):
                if not curr:
                    break
                caller_self = curr.f_locals.get('self')
                if isinstance(caller_self, models.BaseModel):
                    model_name = getattr(caller_self, '_name', '')
                    if model_name == 'clinic.patient':
                        is_clinic = True
                        break
                    elif model_name == 'import.invoices.wizard':
                        is_online = True
                        break
                curr = curr.f_back
        except Exception:
            pass
        finally:
            del frame

        # Auto-assign nature
        nature = False
        if is_clinic or self.env.context.get('from_patient_creation'):
            nature = self.env['res.partner.nature'].search([
                ('code', '=', 'clinic_sales')
            ], limit=1)
        elif is_online or self.env.context.get('from_csv_invoice_import'):
            nature = self.env['res.partner.nature'].search([
                ('code', '=', 'online_sales')
            ], limit=1)

        if nature:
            for vals in vals_list:
                vals.setdefault('nature_id', nature.id)

        return super().create(vals_list)