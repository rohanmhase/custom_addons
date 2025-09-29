{
    "name": "Patient Management",
    "version": "1.0",
    "category": "Healthcare",
    "summary": "Manage patients in hospital",
    "description": """
        Module to manage patients:
        - Create patients
        - Store patient details
    """,
    "author": "Researchayu",
    "depends": ["base", "clinic_management"],
    'data': [
        'security/patient_security.xml',
        'security/ir.model.access.csv',
        'views/patient_views.xml',
        'views/blood_report_views.xml',
        'views/case_taking_views.xml',
    ],
    'assets': {},
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
