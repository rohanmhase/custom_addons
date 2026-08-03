{
    'name': 'Bill of Supply',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'POS invoices as Bill of Supply, Invoicing menu invoices as Tax Invoice',
    'depends': ['account','l10n_in','point_of_sale'],  # POS is optional, hasattr check handles it
    'data': [
        'views/report_invoice_inherit.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}