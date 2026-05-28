{
    'name': 'Clinic Stock Replenishment',
    'version': '1.0',
    'depends': ['stock', 'product', 'mail','mrp',],
    'data': [
        'security/access_views.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/clinic_stock_replenishment_views.xml',
        'views/clinic_region_views.xml',
        'views/clinic_formula_copy_wizard.xml',
        'views/stock_count_formula_views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
