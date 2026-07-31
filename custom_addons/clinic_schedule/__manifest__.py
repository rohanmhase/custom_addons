{
    'name': 'Clinic Patient-Therapist Slotting',
    'version': '17.0.1.0.0',
    'category': 'Clinical',
    'summary': 'Sleek Interactive Custom Matrix Grid Scheduling Dashboard',
    'depends': ['clinic_management', 'patient_management', 'mail', 'product', 'hr'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/clinic_schedule_views.xml',
        'views/menus.xml',
        'data/sequence.xml',
        'data/cron.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'clinic_schedule/static/src/js/clinic_dashboard.js',
            'clinic_schedule/static/src/xml/clinic_dashboard.xml',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
}
