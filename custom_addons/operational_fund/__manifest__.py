{
    'name': 'Operational Fund Management',
    'version': '1.0',
    'category': 'Accounting/Localizations',
    'summary': 'Manage clinic operational accounts, local disbursements, and generate vouchers.',
    'depends': ['base', 'mail','clinic_management'],
    'data': [
        'security/operational_fund_security.xml',
        'security/ir.model.access.csv',
        'views/operational_fund_views.xml',
        'views/operational_fund_menus.xml',
        'report/voucher_report.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}