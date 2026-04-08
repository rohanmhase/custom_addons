# -*- coding: utf-8 -*-
from odoo import models


class PosSession(models.Model):
    _inherit = 'pos.session'

    def _loader_params_product_product(self):
        # 1. Get the standard fields (name, price, etc.)
        result = super()._loader_params_product_product()

        # 2. Add 'type' so the JS patch can actually see it
        result['search_params']['fields'].append('type')

        return result