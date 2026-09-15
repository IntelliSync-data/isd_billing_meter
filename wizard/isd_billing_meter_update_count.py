# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.isd_billing_meter_setup import MONTH_SELECTION, first_day_of_month


class IsdBillingMeterUpdateCount(models.TransientModel):
    _name = 'isd.billing.meter.update.count'
    _description = 'Billing Meter Update Record Count'

    setup_id = fields.Many2one('isd.billing.meter.setup', string='Setup', required=True)
    month = fields.Selection(MONTH_SELECTION, required=True,
                             default=lambda self: str(fields.Date.context_today(self).month))
    year = fields.Integer(required=True, default=lambda self: fields.Date.context_today(self).year)
    current_count = fields.Integer(string='Current Count', compute='_compute_current_count')
    new_count = fields.Integer(string='New Count', compute='_compute_new_count', store=True, readonly=False)

    def _find_snapshot(self):
        self.ensure_one()
        return self.env['isd.billing.meter.snapshot'].search([
            ('setup_id', '=', self.setup_id.id),
            ('year', '=', self.year),
            ('month', '=', self.month),
        ], limit=1)

    @api.depends('setup_id', 'year', 'month')
    def _compute_current_count(self):
        for wizard in self:
            wizard.current_count = wizard._find_snapshot().count

    @api.depends('current_count')
    def _compute_new_count(self):
        for wizard in self:
            wizard.new_count = wizard.current_count

    def action_apply(self):
        self.ensure_one()
        setup = self.setup_id
        period = first_day_of_month(self.year, self.month)
        if not period or not setup._is_in_period(period):
            raise UserError(_('This month is outside the period of "%(name)s" (%(period)s).',
                              name=setup.name, period=setup.period_label))
        if self.new_count < 0:
            raise UserError(_('The record count cannot be negative.'))

        snapshot = self._find_snapshot()
        if snapshot:
            snapshot.count = self.new_count
        else:
            snapshot = self.env['isd.billing.meter.snapshot'].create({
                'setup_id': setup.id,
                'year': self.year,
                'month': self.month,
                'count': self.new_count,
            })
            snapshot.message_post(body=_('Record count set manually to %s.', self.new_count))
        return {'type': 'ir.actions.act_window_close'}
