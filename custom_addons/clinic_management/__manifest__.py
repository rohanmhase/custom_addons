{
    "name": "Clinic Management",
    "version": "1.0",
    "category": "Healthcare",
    "summary": "Manage Clinics Details",
    "description": """
        Module to manage Clinics
    """,
    "author": "ResearchAyu",
    "depends": ["base", "stock", "point_of_sale", "bus", "stock","account"],
    "data": [
        'security/clinic_security.xml',
        'security/ir.model.access.csv',
        'views/clinics_views.xml',
        'views/res_users_views.xml',
        'views/clinic_dashboard_view.xml',
        'views/clinic_transfer_views.xml',
        'views/res_company_views.xml',
        'views/pos_session_edit.xml',
        # 'views/pos_session_view.xml',
    ],
    "assets": {
        'point_of_sale._assets_pos': [
            "clinic_management/static/src/js/product_screen.js",
            "clinic_management/static/src/js/pos_cc_validation.js",
            "clinic_management/static/src/js/pos_store_patch.js",
            "clinic_management/static/src/css/pos_custom.css",
            "clinic_management/static/src/xml/order_receipt.xml",
        ]
    },
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
