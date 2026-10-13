{
    'name': 'Leaderboard',
    'version': '1.0',
    'summary': 'Daily cached leaderboard with automatic OWL JS popup',
    'category': 'Healthcare',
    'depends': ['base', 'web', 'clinic_management', 'patient_management'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/cron_jobs.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'clinic_leaderboard/static/src/xml/clinic_leaderboard_banner.xml',
            'clinic_leaderboard/static/src/js/clinic_leaderboard_banner.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
