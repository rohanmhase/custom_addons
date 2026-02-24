{
    'name': 'Clinic Stock Replenishment',
    'version': '1.0',
    'depends': ['stock', 'product'],
    'data': [
        'security/access_views.xml',
        'security/ir.model.access.csv',
        'views/clinic_stock_replenishment_views.xml',
        'views/clinic_region_views.xml',
        'views/stock_count_formula_views.xml'
    ],
    'installable': True,
    'application': True,
}
