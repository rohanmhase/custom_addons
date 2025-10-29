from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime, timedelta


class Prescription(models.Model):
    _name = "patient.prescription"
    _description = "Patient Prescription"

    patient_id = fields.Many2one("clinic.patient", string="Patient", required=True, readonly=True)
    doctor_id = fields.Many2one("res.users", string="Doctor",
                                required=True,
                                default=lambda self: self.env.user,
                                readonly=True)
    prescription_date = fields.Date(string="Prescription Date",
                                    default=lambda self: self._ist_date(),
                                    readonly=True)
    clinic_id = fields.Many2one(
        "clinic.clinic",
        string="Clinic",
        required=True,
        related="patient_id.clinic_id"
    )
    line_ids = fields.One2many("patient.prescription.line",
                               "prescription_id",
                               string="Medicines",
                               required=True)

    notes = fields.Char(string="Notes")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("done", "Done"),
        ],
        string="Status",
        default="draft",
    )

    active = fields.Boolean(default=True)


    def _check_has_lines(self):
        """Raise error if no lines exist."""
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("⚠️ You cannot update a prescription without medicines."))


    @api.model
    def get_available_medicines(self, clinic_id):
        """Get all available medicines with stock for the clinic's warehouse"""
        clinic = self.env['clinic.clinic'].browse(clinic_id)
        if not clinic or not clinic.warehouse_id:
            return []

        warehouse = clinic.warehouse_id
        products = self.env['product.product'].search([
            ('type', '=', 'product'),
            ('sale_ok', '=', True),
            ('active', '=', True)
        ])

        medicine_data = []
        for product in products:
            qty_available = product.with_context(
                location=warehouse.lot_stock_id.id
            ).qty_available if warehouse.lot_stock_id else product.qty_available

            medicine_data.append({
                'id': product.id,
                'name': product.display_name,
                'qty_available': qty_available,
                'image': product.image_128,
            })

        return medicine_data

    def _check_stock(self):
        """Check all lines against available stock"""
        self._check_has_lines()
        error_msgs = []
        for line in self.line_ids:
            product = line.product_id
            if product.type != "product":
                continue
            warehouse = self.clinic_id.warehouse_id
            qty_available = product.with_context(
                location=warehouse.lot_stock_id.id
            ).qty_available if warehouse and warehouse.lot_stock_id else product.qty_available

            if line.qty > qty_available:
                error_msgs.append(
                    _("❌ %s is out of stock. Available: %s, Required: %s")
                    % (product.display_name, qty_available, line.qty)
                )

        if error_msgs:
            raise UserError("\n".join(error_msgs))


    # ------------------ OVERRIDE CREATE ------------------ #
    @api.model
    def create(self, vals):
        if not vals.get('line_ids'):
            raise UserError(_("⚠️ You cannot create a prescription without medicines."))
        record = super().create(vals)
        # Check stock immediately after creation
        record._check_stock()
        return record

    # ------------------ CONFIRM ACTION ------------------ #
    def action_confirm(self):
        for rec in self:
            rec._check_has_lines()
            if rec.state == "done":
                raise UserError(_("⚠️ You cannot reconfirm a prescription that is already %s.") % rec.state)
            if not rec.line_ids:
                raise UserError(_("⚠️ You cannot confirm a prescription without medicines."))

            rec._check_stock()  # Reuse same stock validation
            rec.state = "confirmed"

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('✅ Success'),
                'message': _('Prescription confirmed successfully!'),
                'type': 'success',
                'sticky': False,
            }
        }

    # ------------------ OVERRIDE WRITE ------------------ #
    def write(self, vals):
        for rec in self:
            if rec.state == "done":
                raise UserError(_("⚠️ You cannot update a prescription that is Done."))
        result = super().write(vals)

        for rec in self:
            if rec.state == "confirmed":
                rec._check_stock()  # Ensure stock still available after edit

        return result

    def copy(self, default=None):
        raise UserError(_("⚠️ Duplication of this record is not allowed."))

    @api.model
    def get_latest_prescription(self, patient_id):
        """Return latest confirmed prescription lines for given patient (customer)"""
        patient = self.env["clinic.patient"].search(
            [("partner_id", "=", patient_id)], limit=1
        )
        prescription = self.search(
            [("patient_id", "=", patient.id), ("state", "=", "confirmed")],
            order="id desc",
            limit=1,
        )
        if not prescription:
            return []

        return [
            {
                "id": line.product_id.id,
                "qty": line.qty,
                "name": line.product_id.display_name,
            }
            for line in prescription.line_ids
        ]

    def _ist_date(self):
        utc = (datetime.now())
        td = timedelta(hours=5, minutes=30)
        ist_date = utc + td
        return ist_date.date()

    def unlink(self):
        for record in self:
            record.active = False
        # Do not call super() → prevents actual deletion
        return True


class PrescriptionLine(models.Model):
    _name = "patient.prescription.line"
    _description = "Prescription Line"

    prescription_id = fields.Many2one(
        "patient.prescription", string="Prescription", required=True)
    product_id = fields.Many2one("product.product", string="Medicine", required=True)
    qty = fields.Float(string="Quantity", default=1.0)
    instructions = fields.Selection([('before_food', 'Before Food'),
                                     ('after_food', 'After Food'),
                                     ('at_night_time', 'At Night Time'),
                                     ('10am_6pm', '10 am to 6 pm'),
                                     ('local_application', 'Local Application'),
                                     ], string="Instructions")
    dosage = fields.Selection([('2 - 0 - 2', '2 - 0 - 2'),
                               ('2 - 2 - 2', '2 - 2 - 2'),
                               ('3 - 0 - 3', '3 - 0 - 3'),
                               ('3 - 3 - 3', '3 - 3 - 3'),
                               ('1 - 0 - 1', '1 - 0 - 1'),
                               ('1 - 1 - 1', '1 - 1 - 1'),
                               ('0 - 1 - 0', '0 - 1 - 0'),
                               ('0 - 2 - 0', '0 - 2 - 0'),
                               ('0 - 0 - 1', '0 - 0 - 1'),
                               ('0 - 0 - 2', '0 - 0 - 2'),
                               ('0 - 0 - 3', '0 - 0 - 3'),
                               ('0 - 0 - 4', '0 - 0 - 4'), ],
                              string="Dosage")
    qty_available = fields.Float(
        string="Available Qty", compute="_compute_qty_available", readonly=True
    )

    active = fields.Boolean(default=True)

    @api.depends("product_id", "prescription_id.clinic_id")
    def _compute_qty_available(self):
        for line in self:
            qty = 0.0
            if line.product_id and line.prescription_id.clinic_id:
                warehouse = line.prescription_id.clinic_id.warehouse_id
                if warehouse and warehouse.lot_stock_id:
                    qty = line.product_id.with_context(
                        location=warehouse.lot_stock_id.id
                    ).qty_available
            line.qty_available = qty

