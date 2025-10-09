from odoo import api, fields, models

class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"
    clinic_id = fields.Many2one("clinic.clinic", string="Clinic")

class PosConfig(models.Model):
    _inherit = "pos.config"
    clinic_id = fields.Many2one("clinic.clinic", string="Clinic")

    def get_limited_partners_loading(self):
        """Override to filter partner IDs by clinic"""
        result = super().get_limited_partners_loading()

        if self.clinic_id:
            # Get the original partner IDs (list of tuples [(id,), (id,), ...])
            partner_ids = [res[0] for res in result] if result else []

            if partner_ids:
                # Search partners that are in the list AND belong to this clinic
                filtered_partners = self.env['res.partner'].search([
                    ('id', 'in', partner_ids),
                    ('clinic_id', '=', self.clinic_id.id)
                ])
                result = [(partner.id,) for partner in filtered_partners]

        return result


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _get_partners_domain(self):
        """Override to add clinic filter"""
        domain = super()._get_partners_domain()

        # Add clinic filter if POS has a clinic assigned
        if self.config_id.clinic_id:
            domain.append(('clinic_id', '=', self.config_id.clinic_id.id))
        return domain


    def _loader_params_res_partner(self):
        result = super()._loader_params_res_partner()

        # Add clinic_id to the fields that will be loaded
        if 'clinic_id' not in result['search_params']['fields']:
            result['search_params']['fields'].append('clinic_id')

        return result
