{
    'name': 'ONLINE SALES INVOICE IMPORTER',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Excel import for batched invoices, global discounts, and auto-payments.',
    'depends': ['account'],
    'data': [
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        'wizard/wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}