# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .isd_billing_meter_setup import MONTH_SELECTION, compute_billing, first_day_of_month


class IsdBillingMeterSnapshot(models.Model):
    _name = 'isd.billing.meter.snapshot'
    _description = 'Billing Meter Monthly Snapshot'
    _inherit = ['mail.thread']
    _order = 'period_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    setup_id = fields.Many2one('isd.billing.meter.setup', string='Setup', required=True,
                               ondelete='cascade', index=True)
    module_id = fields.Many2one(related='setup_id.module_id', store=True)
    year = fields.Integer(required=True)
    month = fields.Selection(MONTH_SELECTION, string='Calendar Month', required=True)
    period_date = fields.Date(string='Period', compute='_compute_period', store=True, index=True)
    period_label = fields.Char(string='Month', compute='_compute_period', store=True)

    count = fields.Integer(string='Record Count', tracking=True)
    free_quota = fields.Integer(string='Free Records', compute='_compute_billing', store=True)
    excess = fields.Integer(string='Excess Records', compute='_compute_billing', store=True)
    packs = fields.Integer(string='Packs', compute='_compute_billing', store=True)
    cost = fields.Monetary(string='Cost', compute='_compute_billing', store=True, currency_field='currency_id')
    currency_id = fields.Many2one(related='setup_id.currency_id', store=True)

    collected_by = fields.Many2one('res.users', string='Last Collected By', readonly=True)
    collected_at = fields.Datetime(string='Last Collected At', readonly=True)

    _sql_constraints = [
        ('setup_period_unique', 'unique(setup_id, year, month)',
         'This setup already has a snapshot for this month.'),
    ]

    @api.depends('setup_id.name', 'period_label')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s - %s' % (rec.setup_id.name or '', rec.period_label or '')

    @api.depends('year', 'month')
    def _compute_period(self):
        for rec in self:
            rec.period_date = first_day_of_month(rec.year, rec.month)
            rec.period_label = rec.period_date.strftime('%m/%Y') if rec.period_date else False

    @api.depends('count', 'setup_id.limit_record', 'setup_id.promotion', 'setup_id.price')
    def _compute_billing(self):
        for rec in self:
            rec.free_quota, rec.excess, rec.packs, rec.cost = compute_billing(
                rec.count, rec.setup_id.limit_record, rec.setup_id.promotion, rec.setup_id.price)

    def _check_manual_count_access(self):
        # Collect writes with sudo; any other change of the count is a manual update
        if self.env.su or self.env.user.has_group('isd_billing_meter.group_billing_meter_it_support'):
            return
        raise AccessError(_('Only IT Support can change the record count manually.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._check_manual_count_access()
        return super().create(vals_list)

    def write(self, vals):
        if 'count' in vals:
            self._check_manual_count_access()
        return super().write(vals)

    def action_open_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
        }

    def action_open_update_count(self):
        self.ensure_one()
        action = self.setup_id.action_open_update_count()
        action['context'].update({'default_year': self.year, 'default_month': self.month})
        return action
