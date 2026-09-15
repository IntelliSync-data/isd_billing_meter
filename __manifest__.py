{
    'name': 'Billing Meter (by ISD)',
    'version': '18.0.1.0.0',
    'category': 'ISD Modules',
    'summary': 'Bill customers by the number of records in each module menu',
    'depends': ['base', 'web', 'mail'],
    'data': [
        'security/isd_billing_meter_security.xml',
        'security/ir.model.access.csv',
        'wizard/isd_billing_meter_update_count_views.xml',
        'views/isd_billing_meter_snapshot_views.xml',
        'views/isd_billing_meter_setup_views.xml',
        'views/isd_billing_meter_menus.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
