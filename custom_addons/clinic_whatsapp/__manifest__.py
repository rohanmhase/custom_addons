{
    'name': 'Clinic WhatsApp Integration',
    'version': '1.0',
    'category': 'Healthcare',
    'summary': 'Send WhatsApp notifications for bills and therapy appointments',
    'depends': ['base', 'account', 'clinic_schedule'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/res_config_settings_views.xml',
        'views/whatsapp_message_queue_views.xml',
        'views/whatsapp_template_views.xml',

    ],
    'installable': True,
    'license': 'LGPL-3',
}