import io
import zipfile

from odoo import http
from odoo.http import request, content_disposition

from ..models.mybillbook_invoice import MAX_SEPARATE_PDFS

REPORT_REF = 'mybillbook_invoices.action_report_mybillbook_invoice'


class MybillbookDownload(http.Controller):

    @http.route('/mybillbook/download_separate', type='http', auth='user')
    def download_separate(self, ids='', **kw):
        try:
            id_list = [int(i) for i in ids.split(',') if i.strip()]
        except ValueError:
            return request.not_found()

        invoices = request.env['mybillbook.invoice'].with_context(active_test=False).browse(id_list).exists()
        if not invoices or len(invoices) > MAX_SEPARATE_PDFS:
            return request.not_found()
        invoices.check_access_rights('read')
        invoices.check_access_rule('read')

        Report = request.env['ir.actions.report']
        files, used = [], {}
        for inv in invoices:
            pdf, _fmt = Report._render_qweb_pdf(REPORT_REF, [inv.id])
            base = inv.pdf_filename()
            n = used.get(base, 0)
            used[base] = n + 1
            name = base if not n else '%s (%d)' % (base, n + 1)
            files.append((name + '.pdf', pdf))

        if len(files) == 1:
            fname, content = files[0]
            return request.make_response(content, headers=[
                ('Content-Type', 'application/pdf'),
                ('Content-Length', len(content)),
                ('Content-Disposition', content_disposition(fname)),
            ])

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname, content in files:
                zf.writestr(fname, content)
        data = buf.getvalue()
        return request.make_response(data, headers=[
            ('Content-Type', 'application/zip'),
            ('Content-Length', len(data)),
            ('Content-Disposition', content_disposition('Invoices.zip')),
        ])
