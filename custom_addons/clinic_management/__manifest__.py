{
    "name": "Clinic Management",
    "version": "1.0",
    "category": "Healthcare",
    "summary": "Manage Clinics Details",
    "description": """
        Module to manage Clinics
    """,
    "author": "ResearchAyu",
    "depends": ["base", "stock", "point_of_sale", "bus"],
    "data": [
        'security/clinic_security.xml',
        'security/ir.model.access.csv',
        'views/clinics_views.xml',
        'views/res_users_views.xml',
        'views/clinic_dashboard_view.xml'
    ],
    "assets": {
        'point_of_sale._assets_pos': [
            "clinic_management/static/src/js/product_screen.js",]
    },
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
