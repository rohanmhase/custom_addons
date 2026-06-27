from odoo import models, fields, api, _
from odoo.exceptions import UserError


# ─────────────────────────────────────────────────────────────────────────────
# 1.  MANUAL MEDICINE COST
#     One record per product. The unit_cost here replaces standard_price so
#     we never expose the real purchase cost in the report SQL.
# ─────────────────────────────────────────────────────────────────────────────
class ClinicPerformanceMedicineCost(models.Model):
    _name = 'clinic.performance.medicine.cost'
    _description = 'Manual Medicine Unit Cost'
    _rec_name = 'product_id'
    _order = 'product_id'

    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        domain=[('type', '=', 'product')],
    )
    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Product Template',
        related='product_id.product_tmpl_id',
        store=False,
        readonly=True,
    )
    unit_cost = fields.Float(
        string='Unit Cost',
        required=True,
        digits=(16, 4),
        help='This cost is used instead of standard price when calculating medicine cost in the Clinic Performance Report.',
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
    )
    notes = fields.Char(string='Notes')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'unique_product',
            'unique(product_id)',
            'A manual cost entry already exists for this product. Edit the existing one.',
        )
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 2.  CLINIC RENT AGREEMENT  (many-to-many with clinic_clinic)
#     • 1 clinic  → 2 rents  (e.g. Dadar Room-1 rent + Room-2 rent)
#     • 1 rent    → 2 clinics (if a single lease covers two branches)
# ─────────────────────────────────────────────────────────────────────────────
class ClinicPerformanceRent(models.Model):
    _name = 'clinic.performance.rent'
    _description = 'Clinic Rent Agreement'
    _rec_name = 'clinic_id'
    _order = 'clinic_id'

    # Single clinic per agreement. Two agreements on the same clinic are
    # summed together when the Clinic Performance Report is generated.
    clinic_id = fields.Many2one(
        'clinic.clinic',
        string='Clinic',
        required=True,
        help='The clinic this rent agreement applies to.',
    )
    address = fields.Text(string='Address', required=True)
    owner_ids = fields.One2many(
        'clinic.performance.rent.owner',
        'rent_id',
        string='Owners',
        help='One or more owners for this rented premises.',
    )
    amount = fields.Float(
        string='Monthly Rent Amount',
        required=True,
        digits=(16, 2),
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Currency',
        default=lambda self: self.env.company.currency_id,
    )
    effective_date = fields.Date(
        string='Effective From',
        help='Leave blank to apply from the beginning of time.',
    )
    expiry_date = fields.Date(
        string='Expiry / End Date',
        help='Leave blank if the agreement is open-ended.',
    )
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notes')

    @api.constrains('effective_date', 'expiry_date')
    def _check_dates(self):
        for rec in self:
            if rec.effective_date and rec.expiry_date:
                if rec.effective_date > rec.expiry_date:
                    raise models.ValidationError(
                        'Effective From date cannot be after Expiry Date.'
                    )

    @api.constrains('owner_ids')
    def _check_owners(self):
        for rec in self:
            if not rec.owner_ids:
                raise models.ValidationError(
                    'Please add at least one owner for this rent agreement.'
                )


class ClinicPerformanceRentOwner(models.Model):
    _name = 'clinic.performance.rent.owner'
    _description = 'Clinic Rent Agreement Owner'
    _order = 'name'

    rent_id = fields.Many2one(
        'clinic.performance.rent',
        string='Rent Agreement',
        required=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(string='Owner Name', required=True)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  CLINIC PERFORMANCE REPORT  (wizard → header → lines)
# ─────────────────────────────────────────────────────────────────────────────

class ClinicPerformanceWizard(models.TransientModel):
    """Step 1 – date range + optional clinic filter."""
    _name = 'clinic.performance.wizard'
    _description = 'Clinic Performance Report – Run Wizard'

    start_date = fields.Date(string='From Date', required=True)
    end_date = fields.Date(string='To Date', required=True)
    clinic_ids = fields.Many2many(
        'clinic.clinic',
        string='Clinics (optional)',
        help='Select one or more clinics. Leave blank to include all clinics.',
    )
    employee_cost_file = fields.Binary(
        string='Employee Cost CSV (optional)',
        help='CSV with columns: Clinic Name, Employee cost. '
             'Used to fill the Employee Cost column. Leave blank to skip.',
    )
    employee_cost_filename = fields.Char(string='Filename')

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.start_date > rec.end_date:
                raise models.ValidationError('"From Date" must be before "To Date".')

    def action_run_report(self):
        self.ensure_one()
        report = self.env['clinic.performance.report'].create({
            'start_date': self.start_date,
            'end_date': self.end_date,
            'clinic_ids': [(6, 0, self.clinic_ids.ids)],
            'clinic_name_filter': ', '.join(self.clinic_ids.mapped('name')),
        })
        unmatched = report._generate_lines(
            employee_cost_file=self.employee_cost_file,
        )

        open_report = report.action_open_lines()

        # Build a combined non-blocking warning (skipped products + unmatched CSV rows)
        messages = []
        if report.skipped_product_count:
            preview = report.skipped_product_names or ''
            if len(preview) > 300:
                preview = preview[:300] + '…'
            messages.append(
                _('%(n)s product(s) had no Medicine Cost set and were excluded:\n%(list)s')
                % {'n': report.skipped_product_count, 'list': preview}
            )
        if unmatched:
            up = ', '.join(unmatched)
            if len(up) > 300:
                up = up[:300] + '…'
            messages.append(
                _('%(n)s Employee-Cost CSV row(s) did not match any clinic and were ignored:\n%(list)s')
                % {'n': len(unmatched), 'list': up}
            )

        if messages:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Report generated with notices'),
                    'message': '\n\n'.join(messages),
                    'type': 'warning',
                    'sticky': True,
                    'next': open_report,
                },
            }

        return open_report


class ClinicPerformanceReport(models.Model):
    """Step 2 – persistent header record that holds the generated lines.

    Made persistent (models.Model) so every run is saved and can be browsed
    later via the History view instead of re-running the report each time.
    """
    _name = 'clinic.performance.report'
    _description = 'Clinic Performance Report'
    _order = 'create_date desc'

    name = fields.Char(
        string='Reference',
        compute='_compute_name',
        store=True,
    )
    start_date = fields.Date(string='From Date', readonly=True)
    end_date = fields.Date(string='To Date', readonly=True)
    clinic_name_filter = fields.Char(string='Clinic Filter', readonly=True)
    clinic_ids = fields.Many2many(
        'clinic.clinic', string='Clinics', readonly=True)
    run_on = fields.Datetime(
        string='Generated On',
        readonly=True,
        default=fields.Datetime.now,
    )
    run_by_id = fields.Many2one(
        'res.users',
        string='Generated By',
        readonly=True,
        default=lambda self: self.env.user,
    )
    line_ids = fields.One2many(
        'clinic.performance.report.line',
        'report_id',
        string='Clinic Lines',
        readonly=True,
    )

    # Products that were moved but had no manual cost entry, so they were
    # EXCLUDED from the medicine cost. Used to warn the user (does not affect
    # the figures shown in the report).
    skipped_product_count = fields.Integer(
        string='Uncosted Products', readonly=True, default=0)
    skipped_product_names = fields.Text(
        string='Uncosted Product List', readonly=True)

    # ── 3-stage delete support ──────────────────────────────────────────────
    # Stage 1: archive  -> active = False
    # Stage 2: delete from the archived view -> ui_hidden = True (row stays in DB)
    active = fields.Boolean(default=True)
    ui_hidden = fields.Boolean(
        string='Hidden from UI',
        default=False,
        help='When set, the record is hidden from every UI view but the row is '
             'kept in the database for audit purposes.',
    )

    @api.depends('start_date', 'end_date', 'clinic_name_filter')
    def _compute_name(self):
        for rec in self:
            period = '%s → %s' % (rec.start_date or '?', rec.end_date or '?')
            if rec.clinic_name_filter:
                rec.name = 'Clinic Performance (%s) [%s]' % (period, rec.clinic_name_filter)
            else:
                rec.name = 'Clinic Performance (%s)' % period

    # ── 3-stage delete actions ──────────────────────────────────────────────
    def action_archive(self):
        """Stage 1 – soft delete: archive the record."""
        self.write({'active': False})

    def action_unarchive(self):
        """Restore an archived record back to the active list."""
        self.write({'active': True})

    def action_ui_delete(self):
        """Stage 2 – delete from UI: hide from every view but keep the DB row.

        Only allowed on records that are already archived, so the flow is
        forced to be: archive first, then UI-delete from the archived view.
        """
        for rec in self:
            if rec.active:
                raise UserError(_(
                    'Please archive this report first. '
                    'Records can only be removed from the UI from the Archived view.'
                ))
        self.write({'ui_hidden': True})

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        """Always exclude UI-hidden rows from every search / view."""
        domain = list(domain or [])
        if not self.env.context.get('include_ui_hidden'):
            # Append the leaf; a flat list of leaves is implicitly AND-ed,
            # so this stays valid even when `domain` is empty.
            domain = domain + [('ui_hidden', '=', False)]
        return super()._search(
            domain, offset=offset, limit=limit, order=order,
            access_rights_uid=access_rights_uid,
        )

    def action_open_lines(self):
        """Open this report's lines in a full list view with a search bar.
        This is the primary view shown after generating a report."""
        self.ensure_one()
        period = '%s → %s' % (self.start_date or '?', self.end_date or '?')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Clinic Performance (%s)') % period,
            'res_model': 'clinic.performance.report.line',
            'view_mode': 'tree',
            'views': [
                (self.env.ref(
                    'account_automation.view_clinic_performance_report_line_full_tree'
                ).id, 'tree'),
            ],
            'search_view_id': [
                self.env.ref(
                    'account_automation.view_clinic_performance_report_line_search'
                ).id
            ],
            'domain': [('report_id', '=', self.id)],
            'context': {'create': False, 'edit': False, 'delete': False},
            'target': 'current',
        }

    # ── SQL ──────────────────────────────────────────────────────────────────
    def _generate_lines(self, employee_cost_file=None):
        """Generate report lines. Returns a list of unmatched CSV clinic names
        (empty if no CSV or all matched). Single heavy SQL round-trip + one
        light skipped-products query; employee-cost matching is done in Python."""
        self.ensure_one()
        self.line_ids.unlink()

        # Build optional clinic filter (by selected clinic IDs) safely
        clinic_filter_clause = (
            "WHERE cc.id IN %(clinic_ids)s"
            if self.clinic_ids
            else ''
        )
        params = {
            'start_date': self.start_date,
            'end_date': self.end_date,
            'clinic_ids': tuple(self.clinic_ids.ids) or (0,),
        }

        query = f"""
            WITH

            clinic_master AS (
                SELECT
                    cc.id               AS clinic_id,
                    cc.name             AS clinic_name,
                    cc.pos_config_id    AS pos_config_id,
                    sw.lot_stock_id     AS stock_location_id
                FROM clinic_clinic cc
                LEFT JOIN stock_warehouse sw ON sw.id = cc.warehouse_id
                {clinic_filter_clause}
            ),

            sales_raw AS (
                SELECT
                    pc.id AS pos_config_id,
                    CASE
                        WHEN ROW_NUMBER() OVER (PARTITION BY am.id ORDER BY pay.method_name) = 1
                        THEN CASE
                                WHEN am.move_type = 'out_refund' THEN -ABS(am.amount_total)
                                ELSE ABS(am.amount_total)
                             END
                        ELSE 0
                    END AS invoice_total
                FROM account_move am
                LEFT JOIN pos_order   po  ON po.account_move = am.id
                LEFT JOIN pos_session pos ON pos.id          = po.session_id
                LEFT JOIN pos_config  pc  ON pc.id           = pos.config_id
                LEFT JOIN (
                    SELECT
                        am_inner.id AS inv_id,
                        COALESCE(
                            ppm.name->>'en_US',
                            CASE
                                WHEN aj.name->>'en_US' IN ('Manual', 'Point of Sale') THEN apml.name
                                ELSE aj.name->>'en_US'
                            END,
                            apml.name,
                            aj.name::text
                        ) AS method_name
                    FROM account_move am_inner
                    JOIN  account_move_line          aml_inv  ON aml_inv.move_id   = am_inner.id
                    JOIN  account_account            acc      ON acc.id             = aml_inv.account_id
                    JOIN  account_partial_reconcile  apr      ON (
                              apr.debit_move_id  = aml_inv.id
                           OR apr.credit_move_id = aml_inv.id
                    )
                    JOIN  account_move_line aml_pay ON (
                              (aml_pay.id = apr.credit_move_id AND aml_pay.id <> aml_inv.id)
                           OR (aml_pay.id = apr.debit_move_id  AND aml_pay.id <> aml_inv.id)
                    )
                    JOIN  account_journal                aj    ON aj.id   = aml_pay.journal_id
                    LEFT JOIN account_payment            ap    ON ap.id   = aml_pay.payment_id
                    LEFT JOIN account_payment_method_line apml ON apml.id = ap.payment_method_line_id
                    LEFT JOIN pos_payment                pp    ON pp.account_move_id = aml_pay.move_id
                    LEFT JOIN pos_payment_method         ppm   ON ppm.id = pp.payment_method_id
                    WHERE am_inner.state   = 'posted'
                      AND acc.account_type = 'asset_receivable'
                ) pay ON pay.inv_id = am.id
                WHERE am.move_type   IN ('out_invoice', 'out_refund')
                  AND am.state        = 'posted'
                  AND am.invoice_date >= %(start_date)s
                  AND am.invoice_date <= %(end_date)s
            ),

            sales_cte AS (
                SELECT pos_config_id, SUM(invoice_total) AS total_sales
                FROM sales_raw
                GROUP BY pos_config_id
            ),

            therapy_cte AS (
                SELECT
                    COALESCE(pses.therapy_clinic_id, cp.clinic_id)       AS clinic_id,
                    COUNT(*)                                              AS therapy_count,
                    COUNT(*) FILTER (WHERE pses.session_type = 'home')   AS home_count,
                    COUNT(*) FILTER (WHERE pses.session_type = 'clinic') AS clinic_count,
                    COUNT(*) FILTER (WHERE pses.session_type = 'self')   AS self_count
                FROM patient_session pses
                LEFT JOIN clinic_patient cp
                       ON cp.id                  = pses.patient_id
                      AND pses.therapy_clinic_id IS NULL
                WHERE pses.session_date >= %(start_date)s
                  AND pses.session_date <= %(end_date)s
                GROUP BY COALESCE(pses.therapy_clinic_id, cp.clinic_id)
            ),

            therapist_cte AS (
                SELECT
                    COALESCE(pses.therapy_clinic_id, cp.clinic_id) AS clinic_id,
                    COUNT(DISTINCT ct.contact_number)               AS therapist_count
                FROM patient_session pses
                LEFT JOIN clinic_patient   cp ON cp.id             = pses.patient_id
                                             AND pses.therapy_clinic_id IS NULL
                JOIN  clinic_therapist     ct ON ct.id             = pses.therapist_id
                WHERE pses.session_date >= %(start_date)s
                  AND pses.session_date <= %(end_date)s
                  AND ct.contact_number IS NOT NULL
                  AND ct.contact_number <> ''
                  AND pses.session_type != 'self'
                GROUP BY COALESCE(pses.therapy_clinic_id, cp.clinic_id)
            ),

            /* ── Medicine cost uses our manual cost table, NOT ir_property ── */
            /* ── Medicine cost from MANUAL cost table only.
                  Products without a manual cost are handled afterwards in
                  Python (fallback to product standard_price). ── */
            medicine_cte AS (
                SELECT
                    cc.id AS clinic_id,
                    SUM(sm.quantity * COALESCE(mc.unit_cost, 0)) AS medicine_cost
                FROM clinic_clinic cc
                JOIN stock_warehouse          sw  ON sw.id               = cc.warehouse_id
                JOIN stock_picking            sp  ON sp.location_dest_id = sw.lot_stock_id
                JOIN stock_picking_type       spt ON spt.id              = sp.picking_type_id
                JOIN stock_move               sm  ON sm.picking_id       = sp.id
                JOIN product_product          pp  ON pp.id               = sm.product_id
                JOIN product_template         pt  ON pt.id               = pp.product_tmpl_id
                LEFT JOIN clinic_performance_medicine_cost mc
                       ON mc.product_id = pp.id
                      AND mc.active     = true
                WHERE spt.code       = 'internal'
                  AND sp.state       = 'done'
                  AND pt.type        = 'product'
                  AND sp.date_done::date >= %(start_date)s
                  AND sp.date_done::date <= %(end_date)s
                GROUP BY cc.id
            ),

            doctor_cte AS (
                SELECT
                    COALESCE(pses.therapy_clinic_id, cp.clinic_id) AS clinic_id,
                    COUNT(DISTINCT pses.doctor_id)                  AS rs_doctor_count
                FROM patient_session pses
                LEFT JOIN clinic_patient cp
                       ON cp.id                  = pses.patient_id
                      AND pses.therapy_clinic_id IS NULL
                WHERE pses.session_date >= %(start_date)s
                  AND pses.session_date <= %(end_date)s
                  AND pses.doctor_id IS NOT NULL
                GROUP BY COALESCE(pses.therapy_clinic_id, cp.clinic_id)
            ),

            /* ── Rent: sum all active agreements that overlap the period ── */
            rent_cte AS (
                SELECT
                    r.clinic_id                  AS clinic_id,
                    SUM(r.amount)                AS total_rent
                FROM clinic_performance_rent r
                WHERE r.active = true
                  AND (r.effective_date IS NULL OR r.effective_date <= %(end_date)s)
                  AND (r.expiry_date    IS NULL OR r.expiry_date    >= %(start_date)s)
                GROUP BY r.clinic_id
            )

            SELECT
                cm.clinic_id,
                cm.clinic_name,
                COALESCE(s.total_sales,      0) AS total_sales,
                COALESCE(t.therapy_count,    0) AS therapy_count,
                COALESCE(t.home_count,       0) AS home_count,
                COALESCE(t.clinic_count,     0) AS clinic_count,
                COALESCE(t.self_count,       0) AS self_count,
                COALESCE(th.therapist_count, 0) AS therapist_count,
                COALESCE(m.medicine_cost,    0) AS medicine_cost,
                COALESCE(d.rs_doctor_count,  0) AS rs_doctor_count,
                COALESCE(rn.total_rent,      0) AS total_rent
            FROM      clinic_master  cm
            LEFT JOIN sales_cte      s   ON s.pos_config_id  = cm.pos_config_id
            LEFT JOIN therapy_cte    t   ON t.clinic_id      = cm.clinic_id
            LEFT JOIN therapist_cte  th  ON th.clinic_id     = cm.clinic_id
            LEFT JOIN medicine_cte   m   ON m.clinic_id      = cm.clinic_id
            LEFT JOIN doctor_cte     d   ON d.clinic_id      = cm.clinic_id
            LEFT JOIN rent_cte       rn  ON rn.clinic_id     = cm.clinic_id
            ORDER BY cm.clinic_name
        """

        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        # ── Detect moved products that have NO manual cost entry. These are
        #    EXCLUDED from the medicine cost (manual-only policy). We collect
        #    their names so the user can be warned which products were skipped,
        #    without changing the report figures or layout. ──
        skipped_query = """
            SELECT DISTINCT pt.id AS tmpl_id
            FROM clinic_clinic cc
            JOIN stock_warehouse    sw  ON sw.id               = cc.warehouse_id
            JOIN stock_picking      sp  ON sp.location_dest_id = sw.lot_stock_id
            JOIN stock_picking_type spt ON spt.id              = sp.picking_type_id
            JOIN stock_move         sm  ON sm.picking_id       = sp.id
            JOIN product_product    pp  ON pp.id               = sm.product_id
            JOIN product_template   pt  ON pt.id               = pp.product_tmpl_id
            LEFT JOIN clinic_performance_medicine_cost mc
                   ON mc.product_id = pp.id
                  AND mc.active     = true
            WHERE spt.code      = 'internal'
              AND sp.state      = 'done'
              AND pt.type       = 'product'
              AND sp.date_done::date >= %(start_date)s
              AND sp.date_done::date <= %(end_date)s
              AND mc.id IS NULL          -- only products with NO manual cost
        """
        self.env.cr.execute(skipped_query, params)
        skipped_ids = [r['tmpl_id'] for r in self.env.cr.dictfetchall()]
        if skipped_ids:
            names = self.env['product.template'].browse(skipped_ids).mapped('name')
            self.skipped_product_names = ', '.join(sorted(n for n in names if n))
            self.skipped_product_count = len(skipped_ids)
        else:
            self.skipped_product_names = ''
            self.skipped_product_count = 0

        # ── Parse optional Employee Cost CSV (Clinic Name, Employee cost) ──
        # Done purely in Python (no DB hit). Fuzzy match: case-insensitive,
        # trimmed. Returns names from the CSV that matched no clinic.
        emp_cost_by_clinic = {}   # normalized clinic name -> float
        unmatched = []
        if employee_cost_file:
            emp_cost_by_clinic, unmatched = self._parse_employee_cost_csv(
                employee_cost_file, rows,
            )

        def _norm(name):
            return (name or '').strip().lower()

        lines = []
        for row in rows:
            emp = emp_cost_by_clinic.get(_norm(row['clinic_name']), 0.0)
            lines.append((0, 0, {
                'clinic_db_id':     row['clinic_id'],
                'clinic_name':      row['clinic_name'],
                'total_sales':      row['total_sales'],
                'therapy_count':    row['therapy_count'],
                'home_count':       row['home_count'],
                'clinic_count':     row['clinic_count'],
                'self_count':       row['self_count'],
                'therapist_count':  row['therapist_count'],
                'medicine_cost':    row['medicine_cost'],
                'rs_doctor_count':  row['rs_doctor_count'],
                'total_rent':       row['total_rent'],
                'employee_cost':    emp,
            }))
        self.line_ids = lines
        return unmatched

    def _parse_employee_cost_csv(self, file_b64, rows):
        """Parse the uploaded base64 CSV. Returns (mapping, unmatched_names).
        mapping: normalized clinic name -> employee cost (float).
        unmatched_names: CSV clinic names that matched no report clinic."""
        import base64
        import csv
        import io

        # Set of valid clinic names from this report's rows (normalized)
        valid = {(r['clinic_name'] or '').strip().lower(): r['clinic_name']
                 for r in rows}

        try:
            raw = base64.b64decode(file_b64)
            text = raw.decode('utf-8-sig', errors='replace')
        except Exception:
            raise UserError(_('Could not read the Employee Cost CSV file.'))

        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise UserError(_('The Employee Cost CSV appears to be empty.'))

        # Find the two columns flexibly (case-insensitive header match)
        name_col = cost_col = None
        for fn in reader.fieldnames:
            key = (fn or '').strip().lower()
            if key in ('clinic name', 'clinic', 'clinic_name'):
                name_col = fn
            elif key in ('employee cost', 'employee_cost', 'employeecost', 'cost'):
                cost_col = fn
        if not name_col or not cost_col:
            raise UserError(_(
                'The Employee Cost CSV must have columns "Clinic Name" and '
                '"Employee cost". Found: %s'
            ) % ', '.join(reader.fieldnames))

        mapping = {}
        unmatched = []
        for line in reader:
            csv_name = (line.get(name_col) or '').strip()
            if not csv_name:
                continue
            norm = csv_name.lower()
            raw_cost = (line.get(cost_col) or '').strip().replace(',', '')
            try:
                cost = float(raw_cost) if raw_cost else 0.0
            except ValueError:
                cost = 0.0
            if norm in valid:
                mapping[norm] = mapping.get(norm, 0.0) + cost
            else:
                unmatched.append(csv_name)

        return mapping, unmatched


class ClinicPerformanceReportLine(models.Model):
    _name = 'clinic.performance.report.line'
    _description = 'Clinic Performance Report – Line'
    _order = 'clinic_name'

    report_id = fields.Many2one(
        'clinic.performance.report',
        string='Report',
        ondelete='cascade',
        index=True,
    )
    clinic_db_id = fields.Integer(string='Clinic DB ID')
    clinic_name = fields.Char(string='Clinic Name')

    # ── Sales ──
    total_sales = fields.Float(string='Sales', digits=(16, 2))

    # ── Therapy ──
    therapy_count = fields.Integer(string='Therapy (Count)')
    home_count = fields.Integer(string='Home')
    clinic_count = fields.Integer(string='Clinic')
    self_count = fields.Integer(string='Self')

    # ── Therapists ──
    therapist_count = fields.Integer(string='Therapist #')

    # ── Medicine cost (manual) ──
    medicine_cost = fields.Float(string='Medicine Cost', digits=(16, 2))

    # ── RS Doctors ──
    rs_doctor_count = fields.Integer(string='RS Numbers')

    # ── Rent (from rent agreements) ──
    total_rent = fields.Float(string='Total Rent', digits=(16, 2))

    # ── Employee cost (from optional CSV upload) ──
    employee_cost = fields.Float(string='Employee Cost', digits=(16, 2))

    # ── Profit / Loss = Sales − (Medicine + Rent + Employee) ──
    profit_loss = fields.Float(
        string='Profit / Loss',
        digits=(16, 2),
        compute='_compute_profit_loss',
        store=True,
    )

    @api.depends('total_sales', 'medicine_cost', 'total_rent', 'employee_cost')
    def _compute_profit_loss(self):
        for rec in self:
            rec.profit_loss = (rec.total_sales or 0.0) - (
                (rec.medicine_cost or 0.0)
                + (rec.total_rent or 0.0)
                + (rec.employee_cost or 0.0)
            )
