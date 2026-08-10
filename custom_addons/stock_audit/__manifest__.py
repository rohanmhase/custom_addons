{
    'name': 'Stock Audit',
    'version': '17.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Per-warehouse stock audit report with Opening, Issue, Receipt, Sale, Closing',
    'description': """
Stock Audit Module
==================
Provides a per-product, per-warehouse audit report showing:
- Opening Quantity
- Issue (internal transfers, inter-company challans, returns to vendor, negative adjustments)
- Receipt (all stock coming into a warehouse)
- Sale (deliveries to real customers)
- Closing Quantity

Features:
- Snapshot-based performance optimization (self-healing cron)
- Region-based warehouse grouping
- Filter by main warehouses / region / specific warehouses / all
- Cross-company visibility (respects parent company hierarchy)
    """,
    'author': 'Your Company',
    'depends': ['stock', 'point_of_sale'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'views/stock_audit_region_views.xml',
        'views/stock_audit_snapshot_views.xml',
        'views/stock_audit_config_views.xml',
        'views/stock_audit_wizard_views.xml',
        'views/stock_audit_menus.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
