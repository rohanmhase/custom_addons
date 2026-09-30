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
            'clinic_leaderboard/static/src/xml/leaderboard_dialog.xml',
            'clinic_leaderboard/static/src/js/clinic_kanban_leaderboard.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
