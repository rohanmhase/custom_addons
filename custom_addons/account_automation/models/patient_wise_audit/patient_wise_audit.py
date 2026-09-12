import io
import base64
import csv
from odoo import models, fields, api, _
from odoo.exceptions import UserError

FRAUD_THRESHOLD = 2000.0


# ─────────────────────────────────────────────────────────────────────────────
# 0.  PRODUCT TYPE CONFIG (Account > Configuration)
# ─────────────────────────────────────────────────────────────────────────────
class PatientAuditProductType(models.Model):
    _name = 'patient.audit.product.type'
    _description = 'Patient Audit – Product Type (Therapy / Medicine / Consultation)'
    _rec_name = 'product_id'
    _order = 'product_id'

    product_id = fields.Many2one(
        'product.product',
        string='Product',
        required=True,
        ondelete='cascade',
    )
    product_tmpl_id = fields.Many2one(
        'product.template',
        string='Product Template',
        related='product_id.product_tmpl_id',
        store=True,
        readonly=True,
    )
    audit_type = fields.Selection(
        [
            ('therapy', 'Therapy'),
            ('treatment', 'Treatment / Medicine'),
            ('consultation', 'Consultation'),
        ],
        string='Audit Type',
        required=True,
        default='therapy',
        help='How this product is classified in Patient Wise Sales vs Medicine Audit.',
    )
    notes = fields.Char(string='Notes')
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'unique_product_audit_type',
            'unique(product_id)',
            'This product is already configured. Edit the existing line.',
        )
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1.  WIZARD
# ─────────────────────────────────────────────────────────────────────────────
class PatientWiseSalesAuditWizard(models.TransientModel):
    _name = 'patient.wise.sales.audit.wizard'
    _description = 'Patient Wise Sales vs Medicine Audit – Run Wizard'

    start_date = fields.Date(string='From Date', required=True)
    end_date = fields.Date(string='To Date', required=True)
    clinic_ids = fields.Many2many(
        'clinic.clinic',
        string='Clinics',
        required=True,
        help='Select one or more clinics (mandatory).',
    )

    pkg_active_during = fields.Boolean(string='Packages Active during period', default=True)
    pkg_completed_during = fields.Boolean(string='Packages Completed during period', default=True)
    pkg_created_during = fields.Boolean(string='Packages Created during period', default=True)

    include_self = fields.Boolean(string='Self', default=True)
    include_clinic = fields.Boolean(string='Clinic', default=True)
    include_home = fields.Boolean(string='Home', default=True)

    mrn_search = fields.Char(
        string='MRN Search',
        help='Partial or exact MRN match (optional).',
    )

    @api.constrains('start_date', 'end_date')
    def _check_dates(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.start_date > rec.end_date:
                raise models.ValidationError('"From Date" must be before "To Date".')

    def action_run_audit(self):
        self.ensure_one()

        types = []
        if self.include_self:
            types.append('self')
        if self.include_clinic:
            types.append('clinic')
        if self.include_home:
            types.append('home')
        if not types:
            raise UserError(_('Select at least one enrollment type.'))

        if not (self.pkg_active_during or self.pkg_completed_during or self.pkg_created_during):
            raise UserError(_('Select at least one option under "Package Lifecycle Selection".'))

        audit = self.env['patient.wise.sales.audit'].create({
            'start_date': self.start_date,
            'end_date': self.end_date,
            'clinic_ids': [(6, 0, self.clinic_ids.ids)],
            'clinic_name_filter': ', '.join(self.clinic_ids.mapped('name')),
            'enrollment_type_filter': ', '.join(types),
            'pkg_active_during': self.pkg_active_during,
            'pkg_completed_during': self.pkg_completed_during,
            'pkg_created_during': self.pkg_created_during,
            'mrn_search_filter': self.mrn_search or '',
        })
        audit._generate_lines(types)

        return {
            'type': 'ir.actions.act_window',
            'name': 'Patient Wise Sales vs Medicine Audit',
            'res_model': 'patient.wise.sales.audit',
            'res_id': audit.id,
            'view_mode': 'form',
            'target': 'current',
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2.  AUDIT HEADER
# ─────────────────────────────────────────────────────────────────────────────
class PatientWiseSalesAudit(models.Model):
    _name = 'patient.wise.sales.audit'
    _description = 'Patient Wise Sales vs Medicine Audit'
    _order = 'create_date desc'

    name = fields.Char(string='Reference', compute='_compute_name', store=True)
    start_date = fields.Date(string='From Date', readonly=True)
    end_date = fields.Date(string='To Date', readonly=True)
    clinic_name_filter = fields.Char(string='Clinic Filter', readonly=True)
    clinic_ids = fields.Many2many('clinic.clinic', string='Clinics', readonly=True)
    enrollment_type_filter = fields.Char(string='Enrollment Types', readonly=True)

    pkg_active_during = fields.Boolean(string='Active during period', readonly=True)
    pkg_completed_during = fields.Boolean(string='Completed during period', readonly=True)
    pkg_created_during = fields.Boolean(string='Created during period', readonly=True)

    mrn_search_filter = fields.Char(string='MRN Filter', readonly=True)

    run_on = fields.Datetime(string='Generated On', readonly=True, default=fields.Datetime.now)
    run_by_id = fields.Many2one('res.users', string='Generated By', readonly=True, default=lambda self: self.env.user)

    line_ids = fields.One2many('patient.wise.sales.audit.line', 'audit_id', string='Audit Lines', readonly=True)

    uncosted_product_count = fields.Integer(string='Uncosted Products', readonly=True, default=0)
    uncosted_product_names = fields.Text(string='Products Without Selling Price', readonly=True)

    active = fields.Boolean(default=True)
    ui_hidden = fields.Boolean(string='Hidden from UI', default=False)

    @api.depends('start_date', 'end_date', 'clinic_name_filter')
    def _compute_name(self):
        for rec in self:
            period = '%s to %s' % (rec.start_date or '?', rec.end_date or '?')
            if rec.clinic_name_filter:
                rec.name = 'Patient Audit (%s) [%s]' % (period, rec.clinic_name_filter)
            else:
                rec.name = 'Patient Audit (%s)' % period

    def action_archive(self):
        self.write({'active': False})

    def action_unarchive(self):
        self.write({'active': True})

    def action_ui_delete(self):
        for rec in self:
            if rec.active:
                raise UserError(
                    _('Please archive this audit first. Records can only be removed from the UI from the Archived view.'))
        self.write({'ui_hidden': True})

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        domain = list(domain or [])
        if not self.env.context.get('include_ui_hidden'):
            domain = domain + [('ui_hidden', '=', False)]
        return super()._search(domain, offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def action_open_lines(self):
        self.ensure_one()
        period = '%s to %s' % (self.start_date or '?', self.end_date or '?')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Patient Audit (%s)') % period,
            'res_model': 'patient.wise.sales.audit.line',
            'view_mode': 'tree',
            'views': [(self.env.ref('account_automation.view_patient_wise_sales_audit_line_full_tree').id, 'tree')],
            'search_view_id': [self.env.ref('account_automation.view_patient_wise_sales_audit_line_search').id],
            'domain': [('audit_id', '=', self.id)],
            'context': {'create': False, 'edit': False, 'delete': False},
            'target': 'current',
        }

    # ─────────────────────────────────────────────────────────────────
    #  EXCEL DOWNLOAD
    # ─────────────────────────────────────────────────────────────────
    def action_download_excel(self):
        self.ensure_one()
        try:
            import xlsxwriter
        except ImportError:
            raise UserError(_('xlsxwriter library is not installed.'))

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet('Patient Audit')

        base_fmt_dict = {'bold': True, 'font_color': '#ffffff', 'border': 1, 'text_wrap': True, 'valign': 'vcenter',
                         'align': 'center'}

        fmt_header_base = workbook.add_format({**base_fmt_dict, 'bg_color': '#374151'})
        fmt_header_a = workbook.add_format({**base_fmt_dict, 'bg_color': '#047857'})
        fmt_header_b = workbook.add_format({**base_fmt_dict, 'bg_color': '#be123c'})
        fmt_header_c = workbook.add_format({**base_fmt_dict, 'bg_color': '#1d4ed8'})
        fmt_header_d = workbook.add_format({**base_fmt_dict, 'bg_color': '#6d28d9'})

        money_fmt = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
        ratio_fmt = workbook.add_format({'num_format': '0.00', 'border': 1})
        int_fmt = workbook.add_format({'num_format': '0', 'border': 1})
        text_fmt = workbook.add_format({'border': 1, 'text_wrap': True})

        fraud_fmt = workbook.add_format({'border': 1, 'text_wrap': True, 'bold': True, 'font_color': '#dc2626'})
        ok_fmt = workbook.add_format({'border': 1, 'text_wrap': True, 'bold': True, 'font_color': '#047857'})

        headers = [
            ('Clinic', fmt_header_base), ('Patient Name', fmt_header_base), ('MRN', fmt_header_base),
            ('Net Sales (A=A1+A2+A3)', fmt_header_a), ('Therapy Sales (A1)', fmt_header_a),
            ('Treatment Sales (A2)', fmt_header_a), ('Cons. Sales (A3)', fmt_header_a),
            ('Earned Therapy Rev (A1_Earned = D1*E2)', fmt_header_a),
            ('Total Cost (B=B1+B2)', fmt_header_b), ('Medicine Cost (B1)', fmt_header_b),
            ('Travel Expense (B2)', fmt_header_b), ('Travel Source (B3)', fmt_header_b),
            ('Total P&L (C=A-B)', fmt_header_c), ('Therapy P&L (C1=A1_Earned-B)', fmt_header_c),
            ('Treatment P&L (C2=A2-B1)', fmt_header_c),
            ('Contracted Price / Session (D1)', fmt_header_d), ('Price Status (D2)', fmt_header_d),
            ('Therapy Ratio (D3=A1_Earned/B)', fmt_header_d), ('Treatment Ratio (D4=A2/B1)', fmt_header_d),
            ('Sessions Bought (E1)', fmt_header_base), ('Sessions Used (E2)', fmt_header_base),
            ('Home Sessions (E3)', fmt_header_base), ('Clinic Sessions (E4)', fmt_header_base),
            ('Self Sessions (E5)', fmt_header_base), ('Total Invoiced (E6)', fmt_header_base),
            ('Credit Notes (E7)', fmt_header_base), ('Enrollment Type (E8)', fmt_header_base),
            ('Enrollment State (E9)', fmt_header_base)
        ]

        for col, (h_name, h_fmt) in enumerate(headers):
            sheet.write(0, col, h_name, h_fmt)

        for row_idx, line in enumerate(self.line_ids, start=1):
            status_style = fraud_fmt if line.fraud_status == 'BELOW STANDARD' else ok_fmt if line.fraud_status == 'Standard-Compliant' else text_fmt

            sheet.write(row_idx, 0, line.clinic_name or '', text_fmt)
            sheet.write(row_idx, 1, line.patient_name or '', text_fmt)
            sheet.write(row_idx, 2, line.mrn or '', text_fmt)
            sheet.write(row_idx, 3, line.net_sales, money_fmt)
            sheet.write(row_idx, 4, line.therapy_sales, money_fmt)
            sheet.write(row_idx, 5, line.treatment_sales, money_fmt)
            sheet.write(row_idx, 6, line.consultation_sales, money_fmt)
            sheet.write(row_idx, 7, line.prorated_therapy_sales, money_fmt)
            sheet.write(row_idx, 8, line.total_cost, money_fmt)
            sheet.write(row_idx, 9, line.medicine_cost, money_fmt)
            sheet.write(row_idx, 10, line.travel_expense, money_fmt)
            sheet.write(row_idx, 11, line.travel_source or '', text_fmt)
            sheet.write(row_idx, 12, line.total_pl, money_fmt)
            sheet.write(row_idx, 13, line.therapy_pl, money_fmt)
            sheet.write(row_idx, 14, line.treatment_pl, money_fmt)
            sheet.write(row_idx, 15, line.contracted_price_session, money_fmt)
            sheet.write(row_idx, 16, line.fraud_status or '', status_style)
            sheet.write(row_idx, 17, line.therapy_ratio, ratio_fmt)
            sheet.write(row_idx, 18, line.treatment_ratio, ratio_fmt)
            sheet.write(row_idx, 19, line.sessions_bought, int_fmt)
            sheet.write(row_idx, 20, line.sessions_used, int_fmt)
            sheet.write(row_idx, 21, line.sessions_home, int_fmt)
            sheet.write(row_idx, 22, line.sessions_clinic, int_fmt)
            sheet.write(row_idx, 23, line.sessions_self, int_fmt)
            sheet.write(row_idx, 24, line.total_invoiced, money_fmt)
            sheet.write(row_idx, 25, line.total_credit_notes, money_fmt)
            sheet.write(row_idx, 26, line.enrollment_type or '', text_fmt)
            sheet.write(row_idx, 27, line.enrollment_state or '', text_fmt)

        widths = [20, 25, 18, 16, 16, 16, 16, 20, 16, 16, 16, 22, 16, 18, 18, 22, 18, 18, 18, 16, 16, 14, 14, 14, 16,
                  16, 16, 16]
        for i, w in enumerate(widths):
            sheet.set_column(i, i, w)

        sheet.freeze_panes(1, 0)
        workbook.close()
        output.seek(0)
        file_data = base64.b64encode(output.read())

        filename = 'Patient_Audit_%s_to_%s.xlsx' % (self.start_date or 'start', self.end_date or 'end')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    # ─────────────────────────────────────────────────────────────────
    #  CSV DOWNLOAD
    # ─────────────────────────────────────────────────────────────────
    def action_download_csv(self):
        self.ensure_one()

        output = io.StringIO()
        writer = csv.writer(output)

        headers = [
            'Clinic', 'Patient Name', 'MRN',
            'Net Sales (A=A1+A2+A3)', 'Therapy Sales (A1)', 'Treatment Sales (A2)',
            'Cons. Sales (A3)', 'Earned Therapy Rev (A1_Earned = D1*E2)',
            'Total Cost (B=B1+B2)', 'Medicine Cost (B1)', 'Travel Expense (B2)', 'Travel Source (B3)',
            'Total P&L (C=A-B)', 'Therapy P&L (C1=A1_Earned-B)', 'Treatment P&L (C2=A2-B1)',
            'Contracted Price / Session (D1)', 'Price Status (D2)',
            'Therapy Ratio (D3=A1_Earned/B)', 'Treatment Ratio (D4=A2/B1)',
            'Sessions Bought (E1)', 'Sessions Used (E2)',
            'Home Sessions (E3)', 'Clinic Sessions (E4)', 'Self Sessions (E5)',
            'Total Invoiced (E6)', 'Credit Notes (E7)',
            'Enrollment Type (E8)', 'Enrollment State (E9)',
        ]
        writer.writerow(headers)

        for line in self.line_ids:
            writer.writerow([
                line.clinic_name or '', line.patient_name or '', line.mrn or '',
                line.net_sales, line.therapy_sales, line.treatment_sales, line.consultation_sales,
                line.prorated_therapy_sales, line.total_cost, line.medicine_cost,
                line.travel_expense, line.travel_source or '',
                line.total_pl, line.therapy_pl, line.treatment_pl,
                line.contracted_price_session, line.fraud_status or '',
                line.therapy_ratio, line.treatment_ratio,
                line.sessions_bought, line.sessions_used, line.sessions_home, line.sessions_clinic,
                line.sessions_self, line.total_invoiced, line.total_credit_notes,
                line.enrollment_type or '', line.enrollment_state or '',
            ])

        file_data = base64.b64encode(output.getvalue().encode('utf-8'))
        filename = 'Patient_Audit_%s_to_%s.csv' % (self.start_date or 'start', self.end_date or 'end')

        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'type': 'binary',
            'datas': file_data,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'text/csv',
        })

        return {
            'type': 'ir.actions.act_url',
            'url': '/web/content/%s?download=true' % attachment.id,
            'target': 'self',
        }

    # ─────────────────────────────────────────────────────────────────
    #  CORE SQL GENERATION
    # ─────────────────────────────────────────────────────────────────
    def _generate_lines(self, enrollment_types):
        self.ensure_one()
        self.line_ids.unlink()

        mrn_clause = "AND (cp.mrn ILIKE %(mrn_search)s OR cp.name ILIKE %(mrn_search)s)" if self.mrn_search_filter else ""
        type_clause = "AND LOWER(COALESCE(pe.enrollment_type::text, '')) IN %(enrollment_types)s" if len(
            enrollment_types) < 3 else ""

        # Construct Package Lifecycle SQL Clause (Mode B)
        lifecycle_conditions = []
        if self.pkg_created_during:
            lifecycle_conditions.append("(pe.enrollment_date >= %(start_date)s AND pe.enrollment_date <= %(end_date)s)")
        if self.pkg_active_during:
            lifecycle_conditions.append("(pe.state = 'active')")
        if self.pkg_completed_during:
            lifecycle_conditions.append("""(pe.state = 'completed' AND EXISTS (
                SELECT 1 FROM patient_session ps 
                WHERE ps.patient_id = pe.patient_id 
                  AND ps.session_date >= %(start_date)s 
                  AND ps.session_date <= %(end_date)s
                  AND COALESCE(ps.active, true) = true
            ))""")

        if lifecycle_conditions:
            lifecycle_clause = "AND (" + " OR ".join(lifecycle_conditions) + ")"
        else:
            lifecycle_clause = "AND 1=0"

        params = {
            'start_date': self.start_date,
            'end_date': self.end_date,
            'clinic_ids': tuple(self.clinic_ids.ids),
            'enrollment_types': tuple(t.lower() for t in enrollment_types) or ('',),
            'mrn_search': f"%{self.mrn_search_filter}%" if self.mrn_search_filter else "",
        }

        raw_query = """
            WITH
            filtered_enrollments AS (
                SELECT pe.*
                FROM patient_enrollment pe
                JOIN clinic_patient cp ON cp.id = pe.patient_id
                JOIN clinic_clinic  cc ON cc.id = cp.clinic_id
                WHERE COALESCE(pe.active, true) = True
                  AND cc.id IN %(clinic_ids)s
                  AND (pe.enrollment_date IS NULL OR pe.enrollment_date <= %(end_date)s)
                  #TYPE_CLAUSE#
                  #LIFECYCLE_CLAUSE#
                  #MRN_CLAUSE#
            ),
            pos_breakdown AS (
                SELECT
                    fe_inner.id AS enrollment_id,
                    SUM(CASE
                        WHEN COALESCE(pat.audit_type, CASE WHEN pt.type = 'service' THEN 'therapy' ELSE NULL END) = 'therapy'
                        THEN pol.price_subtotal_incl ELSE 0 END
                    ) AS pos_therapy_amount,
                    SUM(CASE
                        WHEN COALESCE(pat.audit_type, CASE WHEN pt.type = 'product' THEN 'treatment' ELSE NULL END) = 'treatment'
                        THEN pol.price_subtotal_incl ELSE 0 END
                    ) AS pos_treatment_amount,
                    SUM(CASE
                        WHEN pat.audit_type = 'consultation'
                        THEN pol.price_subtotal_incl ELSE 0 END
                    ) AS pos_cons_amount
                FROM filtered_enrollments fe_inner
                JOIN pos_order po ON po.id = fe_inner.pos_order_id
                JOIN pos_order_line pol ON pol.order_id = po.id
                JOIN product_product pp ON pp.id = pol.product_id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                LEFT JOIN patient_audit_product_type pat
                       ON pat.product_id = pp.id
                      AND pat.active = true
                GROUP BY fe_inner.id
            ),
            patient_master AS (
                SELECT
                    cp.mrn,
                    cp.id               AS clinic_patient_id,
                    cp.partner_id       AS partner_id,
                    cp.name             AS patient_name,
                    cc.id               AS clinic_id,
                    cc.name             AS clinic_name,
                    (ARRAY_AGG(fe.enrollment_type ORDER BY fe.enrollment_date DESC))[1] AS enrollment_type,
                    (ARRAY_AGG(fe.state ORDER BY fe.enrollment_date DESC))[1] AS enrollment_state,
                    SUM(COALESCE(fe.total_sessions, 0)) AS total_sessions_bought,
                    SUM(COALESCE(fe.total_amount, 0)) AS enrol_total_amount,
                    SUM(
                        COALESCE(
                            NULLIF(fe.therapy_amount, 0),
                            NULLIF(pb.pos_therapy_amount, 0),
                            CASE WHEN COALESCE(fe.total_sessions, 0) > 0 THEN fe.total_amount ELSE 0 END
                        )
                    ) AS enrol_therapy_amount,
                    SUM(
                        COALESCE(
                            NULLIF(fe.therapy_medicine, 0),
                            NULLIF(pb.pos_treatment_amount, 0),
                            CASE WHEN COALESCE(fe.total_sessions, 0) = 0 THEN fe.total_amount ELSE 0 END
                        )
                    ) AS enrol_treatment_amount,
                    SUM(
                        COALESCE(
                            NULLIF(fe.first_cons_charges, 0),
                            NULLIF(pb.pos_cons_amount, 0),
                            0
                        )
                    ) AS enrol_cons_amount
                FROM filtered_enrollments fe
                JOIN clinic_patient cp ON cp.id = fe.patient_id
                JOIN clinic_clinic  cc ON cc.id = cp.clinic_id
                LEFT JOIN pos_breakdown pb ON pb.enrollment_id = fe.id
                GROUP BY cp.mrn, cp.id, cp.partner_id, cp.name, cc.id, cc.name
            ),
            session_cte AS (
                SELECT 
                    patient_id AS clinic_patient_id,
                    COUNT(id) AS used_total,
                    COUNT(id) FILTER (WHERE LOWER(session_type) = 'home') AS used_home,
                    COUNT(id) FILTER (WHERE LOWER(session_type) = 'clinic') AS used_clinic,
                    COUNT(id) FILTER (WHERE LOWER(session_type) = 'self') AS used_self
                FROM patient_session
                WHERE COALESCE(active, true) = true
                  AND session_date <= %(end_date)s
                GROUP BY patient_id
            ),
            revenue_cte AS (
                SELECT
                    cp.id AS clinic_patient_id,
                    SUM(CASE WHEN am.move_type = 'out_invoice' THEN am.amount_total ELSE 0 END) AS total_invoiced,
                    SUM(CASE WHEN am.move_type = 'out_refund' THEN ABS(am.amount_total) ELSE 0 END) AS total_credit_notes
                FROM filtered_enrollments fe
                JOIN clinic_patient cp ON cp.id = fe.patient_id
                LEFT JOIN pos_order po ON po.id = fe.pos_order_id
                JOIN account_move am ON (
                    (po.id IS NOT NULL AND am.id = po.account_move)
                    OR (fe.pos_order_id IS NULL AND am.partner_id = cp.partner_id AND am.invoice_date >= fe.enrollment_date AND am.invoice_date <= %(end_date)s)
                )
                WHERE am.state = 'posted'
                  AND am.move_type IN ('out_invoice', 'out_refund')
                  AND am.invoice_date <= %(end_date)s
                GROUP BY cp.id
            ),
            medicine_cte AS (
                SELECT
                    pp.patient_id,
                    SUM(ppl.qty * COALESCE(msp.selling_price, 0)) AS medicine_cost
                FROM patient_prescription pp
                JOIN patient_prescription_line ppl ON ppl.prescription_id = pp.id AND ppl.active = True
                JOIN product_product prod ON prod.id = ppl.product_id
                JOIN product_template pt ON pt.id = prod.product_tmpl_id
                LEFT JOIN medicine_transfer_selling_price msp ON msp.product_id = ppl.product_id AND msp.active = True
                WHERE pp.state = 'done'
                  AND pp.prescription_date <= %(end_date)s
                  AND pt.type = 'product'
                GROUP BY pp.patient_id
            ),
            travel_cte AS (
                SELECT
                    cp2.id AS clinic_patient_id,
                    SUM(ofd.amount) AS travel_cost,
                    COUNT(ofd.id) AS voucher_count
                FROM operational_fund_disbursement ofd
                JOIN clinic_patient cp2 ON cp2.mrn = ofd.home_visit_mrn_search
                WHERE ofd.expense_category = 'travel'
                  AND ofd.travel_type = 'home'
                  AND ofd.state IN ('approved', 'paid')
                  AND ofd.date <= %(end_date)s
                GROUP BY cp2.id
            )
            SELECT
                pm.*,
                COALESCE(s.used_total, 0) AS used_total,
                COALESCE(s.used_home, 0) AS used_home,
                COALESCE(s.used_clinic, 0) AS used_clinic,
                COALESCE(s.used_self, 0) AS used_self,
                COALESCE(r.total_invoiced, 0) AS total_invoiced,
                COALESCE(r.total_credit_notes, 0) AS total_credit_notes,
                COALESCE(r.total_invoiced, 0) - COALESCE(r.total_credit_notes, 0) AS net_sales,
                COALESCE(m.medicine_cost, 0) AS medicine_cost,
                COALESCE(t.travel_cost, 0) AS actual_travel_vouchers,
                COALESCE(t.voucher_count, 0) AS voucher_count
            FROM patient_master pm
            LEFT JOIN session_cte s ON s.clinic_patient_id = pm.clinic_patient_id
            LEFT JOIN revenue_cte r ON r.clinic_patient_id = pm.clinic_patient_id
            LEFT JOIN medicine_cte m ON (m.patient_id = pm.clinic_patient_id OR m.patient_id = pm.partner_id)
            LEFT JOIN travel_cte t ON t.clinic_patient_id = pm.clinic_patient_id
            ORDER BY pm.clinic_name, pm.patient_name
        """

        query = raw_query.replace("#TYPE_CLAUSE#", type_clause).replace("#LIFECYCLE_CLAUSE#", lifecycle_clause).replace(
            "#MRN_CLAUSE#", mrn_clause)
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()

        uncosted_query = """
            SELECT DISTINCT pt.id AS tmpl_id
            FROM patient_prescription pp
            JOIN patient_prescription_line ppl ON ppl.prescription_id = pp.id AND ppl.active = True
            JOIN product_product prod ON prod.id = ppl.product_id
            JOIN product_template pt  ON pt.id  = prod.product_tmpl_id
            LEFT JOIN medicine_transfer_selling_price msp ON msp.product_id = ppl.product_id AND msp.active = True
            WHERE pp.state = 'done'
              AND pp.prescription_date <= %(end_date)s
              AND pt.type = 'product'
              AND msp.id IS NULL
        """
        self.env.cr.execute(uncosted_query, params)
        uncosted_ids = [r['tmpl_id'] for r in self.env.cr.dictfetchall()]
        if uncosted_ids:
            names = self.env['product.template'].browse(uncosted_ids).mapped('name')
            self.uncosted_product_names = ', '.join(sorted(n for n in names if n))
            self.uncosted_product_count = len(uncosted_ids)
        else:
            self.uncosted_product_names = ''
            self.uncosted_product_count = 0

        lines = []
        for row in rows:
            net_sales = row['net_sales'] or 0.0
            total_amt = row['enrol_total_amount'] or 0.0
            therapy_amt = row['enrol_therapy_amount'] or 0.0
            treat_amt = row['enrol_treatment_amount'] or 0.0
            cons_amt = row['enrol_cons_amount'] or 0.0
            bought = row['total_sessions_bought'] or 0
            used = row['used_total'] or 0

            # PART A
            if total_amt > 0:
                prop = net_sales / total_amt
                therapy_sales = therapy_amt * prop
                treatment_sales = treat_amt * prop
                cons_sales = cons_amt * prop
            else:
                therapy_sales = net_sales if bought > 0 else 0.0
                treatment_sales = net_sales if bought == 0 else 0.0
                cons_sales = 0.0

            if bought > 0:
                contracted_price = therapy_amt / bought
                fraud_status = 'BELOW STANDARD' if contracted_price < FRAUD_THRESHOLD else 'Standard-Compliant'

                # Cap Earned Rev multiplier to max sessions bought
                capped_used = min(used, bought)
                prorated_therapy_sales = contracted_price * capped_used
            else:
                contracted_price = 0.0
                fraud_status = 'N/A'
                prorated_therapy_sales = 0.0

            # PART B
            med_cost = row['medicine_cost'] or 0.0
            used_home = row['used_home'] or 0
            actual_vouchers = row['actual_travel_vouchers'] or 0.0
            voucher_count = row['voucher_count'] or 0

            # Hybrid Travel Fallback Logic
            if used_home > 0:
                if voucher_count > 0:
                    unvouchered = max(0, used_home - voucher_count)
                    travel_cost = actual_vouchers + (unvouchered * 60.0)
                    if unvouchered > 0:
                        travel_source = f"Hybrid: {voucher_count} Vouchers + {unvouchered} Std (60)"
                    else:
                        travel_source = 'Actual (Vouchers)'
                else:
                    travel_cost = used_home * 60.0
                    travel_source = 'Standard 60/session (No Vouchers)'
            else:
                travel_cost = 0.0
                travel_source = 'N/A'

            total_cost = med_cost + travel_cost

            # PART C
            total_pl = net_sales - total_cost
            therapy_pl = prorated_therapy_sales - total_cost
            treatment_pl = treatment_sales - med_cost

            # PART D
            therapy_ratio = (prorated_therapy_sales / total_cost) if total_cost else 0.0
            treatment_ratio = (treatment_sales / med_cost) if med_cost else 0.0

            lines.append((0, 0, {
                'mrn': row['mrn'],
                'patient_id': row['clinic_patient_id'],
                'patient_name': row['patient_name'],
                'clinic_name': row['clinic_name'],

                'net_sales': net_sales,
                'therapy_sales': therapy_sales,
                'treatment_sales': treatment_sales,
                'consultation_sales': cons_sales,
                'prorated_therapy_sales': prorated_therapy_sales,

                'medicine_cost': med_cost,
                'travel_expense': travel_cost,
                'travel_source': travel_source,
                'total_cost': total_cost,

                'total_pl': total_pl,
                'therapy_pl': therapy_pl,
                'treatment_pl': treatment_pl,

                'contracted_price_session': contracted_price,
                'fraud_status': fraud_status,
                'therapy_ratio': therapy_ratio,
                'treatment_ratio': treatment_ratio,

                'sessions_bought': bought,
                'sessions_used': used,
                'sessions_home': used_home,
                'sessions_clinic': row['used_clinic'] or 0,
                'sessions_self': row['used_self'] or 0,
                'enrollment_type': row['enrollment_type'],
                'enrollment_state': row['enrollment_state'],
                'total_invoiced': row['total_invoiced'] or 0.0,
                'total_credit_notes': row['total_credit_notes'] or 0.0,
            }))
        self.line_ids = lines


# ─────────────────────────────────────────────────────────────────────────────
# 3.  AUDIT LINE
# ─────────────────────────────────────────────────────────────────────────────
class PatientWiseSalesAuditLine(models.Model):
    _name = 'patient.wise.sales.audit.line'
    _description = 'Patient Wise Sales vs Medicine Audit – Line'
    _order = 'clinic_name, patient_name'

    audit_id = fields.Many2one('patient.wise.sales.audit', string='Audit', ondelete='cascade', index=True)

    mrn = fields.Char(string='MRN')
    patient_id = fields.Integer(string='Patient DB ID')
    patient_name = fields.Char(string='Patient Name')
    clinic_name = fields.Char(string='Clinic')

    net_sales = fields.Float(string='Net Sales (A)', digits=(16, 2))
    therapy_sales = fields.Float(string='Therapy Sales (A1)', digits=(16, 2))
    treatment_sales = fields.Float(string='Treatment Sales (A2)', digits=(16, 2))
    consultation_sales = fields.Float(string='Cons. Sales (A3)', digits=(16, 2))
    prorated_therapy_sales = fields.Float(string='Earned Therapy Rev (A1_Earned)', digits=(16, 2))

    total_cost = fields.Float(string='Total Cost (B)', digits=(16, 2))
    medicine_cost = fields.Float(string='Medicine Cost (B1)', digits=(16, 2))
    travel_expense = fields.Float(string='Travel Expense (B2)', digits=(16, 2))
    travel_source = fields.Char(string='Travel Source (B3)')

    total_pl = fields.Float(string='Total P&L (C)', digits=(16, 2))
    therapy_pl = fields.Float(string='Therapy P&L (C1)', digits=(16, 2))
    treatment_pl = fields.Float(string='Treatment P&L (C2)', digits=(16, 2))

    contracted_price_session = fields.Float(string='Contracted Price / Session (D1)', digits=(16, 2))
    fraud_status = fields.Char(string='Price Status (D2)')
    therapy_ratio = fields.Float(string='Therapy Ratio (D3)', digits=(16, 2))
    treatment_ratio = fields.Float(string='Treatment Ratio (D4)', digits=(16, 2))

    sessions_bought = fields.Integer(string='Sessions Bought (E1)')
    sessions_used = fields.Integer(string='Sessions Used (E2)')
    sessions_home = fields.Integer(string='Home Sessions (E3)')
    sessions_clinic = fields.Integer(string='Clinic Sessions (E4)')
    sessions_self = fields.Integer(string='Self Sessions (E5)')
    total_invoiced = fields.Float(string='Total Invoiced (E6)', digits=(16, 2))
    total_credit_notes = fields.Float(string='Credit Notes (E7)', digits=(16, 2))
    enrollment_type = fields.Char(string='Enrollment Type (E8)')
    enrollment_state = fields.Char(string='Enrollment State (E9)')