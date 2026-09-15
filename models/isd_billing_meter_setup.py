# -*- coding: utf-8 -*-

import math
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

MONTH_SELECTION = [
    ('1', 'January'), ('2', 'February'), ('3', 'March'), ('4', 'April'),
    ('5', 'May'), ('6', 'June'), ('7', 'July'), ('8', 'August'),
    ('9', 'September'), ('10', 'October'), ('11', 'November'), ('12', 'December'),
]


def first_day_of_month(year, month):
    if not year or not month or not 1 <= year <= 9999:
        return False
    return date(year, int(month), 1)


def compute_billing(count, limit, promotion, price):
    """Return (free_quota, excess, packs, cost) for one month."""
    free_quota = limit * promotion
    excess = max(count - free_quota, 0)
    packs = math.ceil(excess / limit) if limit > 0 else 0
    return free_quota, excess, packs, packs * price


class IsdBillingMeterSetup(models.Model):
    _name = 'isd.billing.meter.setup'
    _description = 'Billing Meter Module Setup'
    _order = 'date_from desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    active = fields.Boolean(default=True)

    # Target: Module -> Menu -> Sub Menu
    # Selection instead of Many2one: ir.module.module is only readable by Settings users
    module_name = fields.Selection(selection='_selection_module_name', string='Module', required=True)
    menu_id = fields.Many2one('ir.ui.menu', string='Menu', ondelete='set null')
    submenu_id = fields.Many2one('ir.ui.menu', string='Sub Menu', ondelete='set null')
    target_menu_id = fields.Many2one('ir.ui.menu', compute='_compute_target', store=True)
    res_model = fields.Char(string='Technical Model', compute='_compute_target', store=True)
    res_model_name = fields.Char(string='Record Type', compute='_compute_target', store=True)
    allowed_menu_ids = fields.Many2many(
        'ir.ui.menu', relation='isd_billing_meter_allowed_menu_rel',
        compute='_compute_allowed_menu_ids')
    allowed_submenu_ids = fields.Many2many(
        'ir.ui.menu', relation='isd_billing_meter_allowed_submenu_rel',
        compute='_compute_allowed_submenu_ids')

    # Pricing
    limit_record = fields.Integer(string='Limit Record', required=True, default=100,
                                  help='Number of records in one pack.')
    promotion = fields.Integer(string='Promotion (Free Packs)', default=0,
                               help='Number of free packs before charging.')
    price = fields.Monetary(string='Price / Pack', required=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency', required=True,
                                  default=lambda self: self.env.company.currency_id)

    # Period (month/year only)
    month_from = fields.Selection(MONTH_SELECTION, string='From Month', required=True,
                                  default=lambda self: str(fields.Date.context_today(self).month))
    year_from = fields.Integer(string='From Year', required=True,
                               default=lambda self: fields.Date.context_today(self).year)
    month_to = fields.Selection(MONTH_SELECTION, string='To Month', required=True,
                                default=lambda self: str(fields.Date.context_today(self).month))
    year_to = fields.Integer(string='To Year', required=True,
                             default=lambda self: fields.Date.context_today(self).year)
    date_from = fields.Date(compute='_compute_period', store=True)
    date_to = fields.Date(compute='_compute_period', store=True)
    period_label = fields.Char(string='Period', compute='_compute_period', store=True)

    # Statistics
    snapshot_ids = fields.One2many('isd.billing.meter.snapshot', 'setup_id', string='Monthly Statistics')
    snapshot_count = fields.Integer(string='Statistics', compute='_compute_snapshot_count')
    collect_status = fields.Selection([
        ('collected', 'Collected'),
        ('missing', 'Not Collected'),
        ('out_of_period', 'Out of Period'),
    ], string='This Month', compute='_compute_current')
    current_count = fields.Integer(string='Records (This Month)', compute='_compute_current')
    current_packs = fields.Integer(string='Packs (This Month)', compute='_compute_current')
    current_cost = fields.Monetary(string='Cost (This Month)', compute='_compute_current',
                                   currency_field='currency_id')

    # ---------------------------------------------------------------------
    # Menu helpers
    # ---------------------------------------------------------------------

    @api.model
    def _menu_env(self):
        # full_list: do not hide menus the current user cannot open
        return self.env['ir.ui.menu'].sudo().with_context({'ir.ui.menu.full_list': True})

    @api.model
    def _get_menu_action(self, menu):
        action = menu.sudo().action if menu else False
        if action and action._name == 'ir.actions.act_window' and action.res_model in self.env:
            return action
        return False

    @api.model
    def _get_countable_menus(self, menus):
        """Keep menus that open a list of records, directly or through a descendant."""
        Menu = self._menu_env()
        return menus.filtered(
            lambda m: any(self._get_menu_action(d) for d in Menu.search([('id', 'child_of', m.id)])))

    @api.model
    def _get_module_menus(self, module_name):
        res_ids = self.env['ir.model.data'].sudo().search([
            ('module', '=', module_name),
            ('model', '=', 'ir.ui.menu'),
        ]).mapped('res_id')
        return self._menu_env().browse(res_ids).exists()

    # ---------------------------------------------------------------------
    # Computes
    # ---------------------------------------------------------------------

    @api.depends('module_name', 'menu_id.name', 'submenu_id.name')
    def _compute_name(self):
        module_labels = dict(self._fields['module_name']._description_selection(self.env))
        for rec in self:
            parts = [module_labels.get(rec.module_name), rec.menu_id.name, rec.submenu_id.name]
            rec.name = ' / '.join(p for p in parts if p) or _('New Setup')

    @api.depends('menu_id', 'submenu_id')
    def _compute_target(self):
        for rec in self:
            menu = rec.submenu_id or rec.menu_id
            action = self._get_menu_action(menu)
            rec.target_menu_id = menu
            rec.res_model = action.res_model if action else False
            rec.res_model_name = self.env['ir.model'].sudo()._get(action.res_model).name if action else False

    @api.model
    def _selection_module_name(self):
        # Installed modules owning an app root menu (a menu without parent)
        root_menus = self._menu_env().search([('parent_id', '=', False)])
        names = set(self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.ui.menu'),
            ('res_id', 'in', root_menus.ids),
        ]).mapped('module'))
        names.discard('isd_billing_meter')
        modules = self.env['ir.module.module'].sudo().search([
            ('name', 'in', list(names)),
            ('state', '=', 'installed'),
        ])
        return sorted(((m.name, m.shortdesc) for m in modules), key=lambda item: item[1].lower())

    @api.depends('module_name')
    def _compute_allowed_menu_ids(self):
        Menu = self._menu_env()
        for rec in self:
            if not rec.module_name:
                rec.allowed_menu_ids = False
                continue
            module_menus = self._get_module_menus(rec.module_name)
            app_roots = module_menus.filtered(lambda m: not m.parent_id)
            menus = Menu.search([('parent_id', 'in', app_roots.ids)])
            menus |= module_menus.filtered(lambda m: m.parent_id and not m.parent_id.parent_id)
            rec.allowed_menu_ids = self._get_countable_menus(menus)

    @api.depends('menu_id')
    def _compute_allowed_submenu_ids(self):
        Menu = self._menu_env()
        for rec in self:
            if not rec.menu_id:
                rec.allowed_submenu_ids = False
                continue
            menu_id = rec.menu_id._origin.id
            descendants = Menu.search([('id', 'child_of', menu_id), ('id', '!=', menu_id)])
            rec.allowed_submenu_ids = descendants.filtered(lambda m: self._get_menu_action(m))

    @api.depends('month_from', 'year_from', 'month_to', 'year_to')
    def _compute_period(self):
        for rec in self:
            rec.date_from = first_day_of_month(rec.year_from, rec.month_from)
            rec.date_to = first_day_of_month(rec.year_to, rec.month_to)
            if rec.date_from and rec.date_to:
                rec.period_label = '%s - %s' % (rec.date_from.strftime('%m/%Y'), rec.date_to.strftime('%m/%Y'))
            else:
                rec.period_label = False

    def _compute_snapshot_count(self):
        ids = [rec._origin.id for rec in self if rec._origin.id]
        counts = dict(self.env['isd.billing.meter.snapshot']._read_group(
            [('setup_id', 'in', ids)], ['setup_id'], ['__count']))
        for rec in self:
            rec.snapshot_count = counts.get(rec._origin, 0)

    def _compute_current(self):
        period = self._current_period()
        ids = [rec._origin.id for rec in self if rec._origin.id]
        snapshots = self.env['isd.billing.meter.snapshot'].search([
            ('setup_id', 'in', ids),
            ('period_date', '=', period),
        ])
        by_setup = {snap.setup_id.id: snap for snap in snapshots}
        for rec in self:
            snap = by_setup.get(rec._origin.id)
            rec.current_count = snap.count if snap else 0
            rec.current_packs = snap.packs if snap else 0
            rec.current_cost = snap.cost if snap else 0.0
            if not rec._is_in_period(period):
                rec.collect_status = 'out_of_period'
            else:
                rec.collect_status = 'collected' if snap else 'missing'

    # ---------------------------------------------------------------------
    # Onchanges / constraints
    # ---------------------------------------------------------------------

    @api.onchange('module_name')
    def _onchange_module_name(self):
        self.menu_id = False
        self.submenu_id = False

    @api.onchange('menu_id')
    def _onchange_menu_id(self):
        self.submenu_id = False

    @api.constrains('module_name', 'menu_id', 'submenu_id')
    def _check_target(self):
        for rec in self:
            if not rec.menu_id:
                raise ValidationError(_('Please select a menu.'))
            if not rec.res_model:
                raise ValidationError(_(
                    '"%s" does not open a list of records. Please select a sub menu.',
                    (rec.submenu_id or rec.menu_id).name))

    @api.constrains('limit_record', 'promotion', 'price')
    def _check_pricing(self):
        for rec in self:
            if rec.limit_record <= 0:
                raise ValidationError(_('Limit Record must be greater than 0.'))
            if rec.promotion < 0 or rec.price < 0:
                raise ValidationError(_('Promotion and Price cannot be negative.'))

    @api.constrains('year_from', 'year_to', 'date_from', 'date_to')
    def _check_period(self):
        for rec in self:
            if not (2000 <= rec.year_from <= 2100 and 2000 <= rec.year_to <= 2100):
                raise ValidationError(_('Year must be between 2000 and 2100.'))
            if rec.date_to < rec.date_from:
                raise ValidationError(_('The end month must be after the start month.'))

    @api.constrains('target_menu_id', 'date_from', 'date_to', 'active')
    def _check_overlap(self):
        for rec in self.filtered('active'):
            overlap = self.search([
                ('id', '!=', rec.id),
                ('target_menu_id', '=', rec.target_menu_id.id),
                ('date_from', '<=', rec.date_to),
                ('date_to', '>=', rec.date_from),
            ], limit=1)
            if overlap:
                raise ValidationError(_(
                    '"%(name)s" already has a setup for %(period)s.',
                    name=rec.name, period=overlap.period_label))

    # ---------------------------------------------------------------------
    # Business logic
    # ---------------------------------------------------------------------

    @api.model
    def _current_period(self):
        return fields.Date.context_today(self).replace(day=1)

    def _is_in_period(self, period):
        self.ensure_one()
        return bool(self.date_from and self.date_to and self.date_from <= period <= self.date_to)

    def _count_records(self):
        """Count records the same way the menu lists them (action domain applied)."""
        self.ensure_one()
        action = self._get_menu_action(self.target_menu_id)
        if not action:
            raise UserError(_('The records of "%s" can no longer be counted. Please check its menu.', self.name))
        domain = []
        if action.domain:
            try:
                domain = safe_eval(action.domain, action._get_eval_context(action))
            except Exception as e:
                raise UserError(_('Cannot read the filter of menu "%(name)s": %(error)s', name=self.name, error=e))
        return self.env[action.res_model].sudo().search_count(domain)

    def action_collect(self):
        """Save the current record count for this month (all active setups when none selected)."""
        period = self._current_period()
        setups = self or self.search([])
        in_period = setups.filtered(lambda s: s._is_in_period(period))
        if not in_period:
            raise UserError(_('No setup is active in %s.', period.strftime('%m/%Y')))

        # sudo keeps the current user as author of the tracking message
        Snapshot = self.env['isd.billing.meter.snapshot'].sudo()
        existing = Snapshot.search([('setup_id', 'in', in_period.ids), ('period_date', '=', period)])
        by_setup = {snap.setup_id.id: snap for snap in existing}
        collect_vals = {'collected_by': self.env.uid, 'collected_at': fields.Datetime.now()}
        create_vals = []
        for setup in in_period:
            count = setup._count_records()
            snap = by_setup.get(setup.id)
            if snap:
                snap.write(dict(collect_vals, count=count))
            else:
                create_vals.append(dict(collect_vals, setup_id=setup.id, year=period.year,
                                        month=str(period.month), count=count))
        Snapshot.create(create_vals)

        message = _('Collected %(count)s setup(s) for %(period)s.',
                    count=len(in_period), period=period.strftime('%m/%Y'))
        skipped = len(setups) - len(in_period)
        if skipped:
            message += ' ' + _('%s skipped (out of period).', skipped)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': message,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_open_update_count(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Update Record Count'),
            'res_model': 'isd.billing.meter.update.count',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_setup_id': self.id},
        }

    def action_view_snapshots(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('isd_billing_meter.action_billing_meter_snapshot')
        action.update({
            'name': self.name,
            'domain': [('setup_id', '=', self.id)],
            'context': {'search_default_group_year': 1},
        })
        return action
