{
    "name": "Clinic Management",
    "version": "1.0",
    "category": "Healthcare",
    "summary": "Manage Clinics Details",
    "description": """
        Module to manage Clinics
    """,
    "author": "ResearchAyu",
    "depends": ["base", "stock", "point_of_sale"],
    "data": [
        'security/clinic_security.xml',
        'security/ir.model.access.csv',
        'views/clinics_views.xml',
    ],
    "assets": {},
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
