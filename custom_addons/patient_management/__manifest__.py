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
        'views/daily_followup_views.xml',
        'views/diet_chart_views.xml',
        'views/followup_views.xml',
        'views/enrollment_views.xml',
        'views/session_views.xml',
        'views/xray_views.xml',
    ],
    'assets': {
        'web.assets_backend': ['patient_management/static/src/css/form.css'],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
