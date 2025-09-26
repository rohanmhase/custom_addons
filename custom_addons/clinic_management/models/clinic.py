from odoo import models, fields, api
from datetime import datetime, timedelta

class Clinic(models.Model):
    _name = "clinic.clinic"
    _description = "Clinic"

    name = fields.Char(string="Clinic Name", required=True)
    address = fields.Text(string="Address", required=True)
    code = fields.Char(string="Clinic Code", required=True)
    phone = fields.Char(string="Phone Number", required=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Warehouse", readonly=True)
    pos_config_id = fields.Many2one("pos.config", string="Point of Sale", readonly=True)
    date_created = fields.Datetime(string="Date Created", readonly=True, default=lambda self: self._ist_date())

    _sql_constraints = [
        ('cons_unique_code', 'UNIQUE(code)', 'Code already exists!'),
    ]

    def _ist_date(self):

        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    @api.model
    def create(self, vals):
        clinic = super(Clinic, self).create(vals)

        # 1️⃣ Create Warehouse
        warehouse = self.env["stock.warehouse"].create({
            "name": clinic.name + " " + clinic.code +" Warehouse",
            "code": (clinic.code[:4]).upper(),
            "company_id": self.env.company.id,
            "clinic_id": clinic.id,

        })

        # 2️⃣ Create POS Config
        pos_config = self.env["pos.config"].create({
            "name": clinic.name + " " + clinic.code + " POS",
            "warehouse_id": warehouse.id,
            "company_id": self.env.company.id,
            "clinic_id": clinic.id,
        })

        # 3️⃣ Link them back to Clinic
        clinic.warehouse_id = warehouse.id
        clinic.pos_config_id = pos_config.id

        return clinic
