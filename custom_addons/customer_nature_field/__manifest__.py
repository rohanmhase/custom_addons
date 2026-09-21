{
    'name': 'Customer Nature Field',
    'version': '17.0.1.0.0',
    'category': 'Sales',
    'summary': 'Adds a Nature classification field on Customers (res.partner)',
    'description': """
        Adds a 'Nature' field to Customers to classify them as:
        - Clinic Patient
        - Franchise
        - Online Sales
        - Self Branch

        Auto-fills nature when customers are created from:
        - Patient Management module  → Clinic Patient
        - CSV Invoice Import wizard  → Online Sales

        The field is 100% optional and never blocks any operation.
    """,
    'author': 'Custom',
    'depends': ['base', 'contacts'],
    'data': [
        'security/ir.model.access.csv',
        'data/res_partner_nature_data.xml',
        'views/res_partner_nature_views.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}