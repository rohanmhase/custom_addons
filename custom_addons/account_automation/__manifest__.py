{
    'name': 'Account Automation Utilities',
    'version': '17.0.1.0.0',
    'category': 'Accounting',
    'summary': 'Custom financial automations, PSMR reconciliations, and internal data audits.',
    'depends': ['account', 'point_of_sale'],
    'data': [
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        'views/account_automation_views.xml',  # Load main layout container first
        'views/psmr_reconciliation_views.xml',  # Load apps dependencies second
        'views/psmr_mapping_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}