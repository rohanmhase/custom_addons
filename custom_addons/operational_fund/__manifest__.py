{
    'name': 'Operational Fund Management',
    'version': '1.0',
    'category': 'Accounting/Localizations',
    'summary': 'Manage clinic operational accounts, local disbursements, and generate vouchers.',
    'depends': ['base', 'mail', 'hr', 'clinic_management', 'project', 'web'],
    'data': [
        'security/operational_fund_security.xml',
        'security/ir.model.access.csv',
        'views/operational_fund_views.xml',
        'views/operational_fund_menus.xml',
        'report/voucher_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'operational_fund/static/src/scss/operational_fund.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
