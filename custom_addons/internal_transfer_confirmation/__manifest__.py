{
    'name': 'Internal Transfer Confirmation',
    'version': '17.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Clinic stock and internal transfer confirmation workflow',
    'depends': [
        'base',
        'stock',
        'mail',
        'point_of_sale',
        'clinic_management',
        'clinic_stock_replenishment',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/clinic_stock_confirmation_mail_template.xml',
        'data/ir_cron_auto_confirm.xml',
        'views/clinic_stock_confirmation_views.xml',
        'views/clinic_internal_transfer_confirmation_views.xml',
        'views/clinic_warehouse_report_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'internal_transfer_confirmation/static/src/xml/clinic_stock_confirmation_list.xml',
            'internal_transfer_confirmation/static/src/js/clinic_stock_confirmation_list.js',
        ],
    },
    'installable': True,
    'auto_install': False,
    'application': False,
    'license': 'LGPL-3',
}