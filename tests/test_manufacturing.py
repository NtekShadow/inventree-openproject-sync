"""Process, machine and work hours of build orders, and list custom fields."""

import unittest
from types import SimpleNamespace
from unittest import mock

from openproject_sync import orders
from openproject_sync.orders import BuildAdapter
from openproject_sync.sync import iso_duration, option_links

SETTINGS = {
    'PROCESS_PARAMETER': 'Fertigungsverfahren',
    'MACHINE_PARAMETER': 'Maschine',
    'EXTERNAL_BUILD_TYPE': '',
    'MACHINE_RATES': 'Drehen Knuth; Fräsen Hermle',
    'NO_WORK_RATES': 'Standzeit',
}


def param(name, data):
    return SimpleNamespace(template=SimpleNamespace(name=name), data=data)


class FakeCosts:
    def __init__(self, costs):
        self.costs = costs

    def filter(self, active):
        return self

    def select_related(self, *args):
        return self.costs


def cost(rate_name, units, quantity, amortization=1, description=''):
    return SimpleNamespace(
        rate=SimpleNamespace(name=rate_name, units=units),
        quantity=quantity,
        amortization=amortization,
        description=description,
    )


def build(part_params=(), build_params=(), costs=(), quantity=10, external=False):
    part = SimpleNamespace(
        full_name='SUS Part 1 - turned',
        IPN='100-001',
        parameters=lambda: list(part_params),
        manufacturing_costs=FakeCosts(list(costs)),
    )
    instance = SimpleNamespace(
        part=part, quantity=quantity, external=external, parameters=lambda: list(build_params)
    )
    adapter = BuildAdapter(instance)
    adapter.settings_lookup = SETTINGS.get
    return adapter


def fake_hours(quantity, units):
    return {'hour': 1.0, 'minute': 1 / 60}.get(units) and float(quantity) * {'hour': 1.0, 'minute': 1 / 60}[units]


class BuildManufacturingTest(unittest.TestCase):
    def test_process_from_part_parameter(self):
        self.assertEqual(build([param('fertigungsverfahren', 'Drehen')]).process(), 'Drehen')
        self.assertEqual(build().process(), '')

    def test_machine_only_from_build_order_parameter(self):
        # A machine parameter on the part is ignored: the machine belongs to the build order
        self.assertEqual(build([param('Maschine', 'Knuth')]).machine(), '')
        self.assertEqual(build([param('Maschine', 'Knuth')], [param('Maschine', 'Weiler')]).machine(), 'Weiler')

    @mock.patch.object(orders, 'cost_hours', side_effect=fake_hours)
    def test_main_machine_from_routing(self, _):
        adapter = build(costs=[
            cost('Vorbereitung', 'hour', 1),
            cost('Drehen Knuth', 'minute', 20),
            cost('Fräsen Hermle', 'minute', 5),
            cost('Drehen Knuth', 'minute', 5, description='Entgraten'),
        ])
        # Vorbereitung is no machine; Drehen Knuth has the most time (25 min vs 5 min)
        self.assertEqual(adapter.machine(), 'Drehen Knuth')

    @mock.patch.object(orders, 'cost_hours', side_effect=fake_hours)
    def test_work_hours_and_steps(self, _):
        # Vorbereitung 2 h over 10 parts, Drehen 30 min, Standzeit 1 h (no personnel), Material (no time)
        adapter = build(costs=[
            cost('Vorbereitung', 'hour', 2, amortization=10, description='Rüsten'),
            cost('Drehen Knuth', 'minute', 30),
            cost('Standzeit', 'hour', 1),
            cost('Material', '', 3),
        ])
        # (0.2 + 0.5) * 10 = 7 h, Standzeit not counted
        self.assertEqual(adapter.work_hours(), 7.0)
        table = adapter.details_markdown()
        self.assertIn('| Rüsten | Vorbereitung | 0,20 h | 2,00 h |  |', table)
        self.assertIn('| Standzeit | Standzeit | 1,00 h | 10,00 h | ohne Personal |', table)
        self.assertIn('| Material | Material | - | - |  |', table)
        self.assertIsNone(build().work_hours())
        self.assertEqual(build().details_markdown(), '')

    def test_manufacturing_and_type(self):
        self.assertEqual(build().manufacturing(), 'Eigenfertigung')
        external = build(external=True)
        self.assertEqual(external.manufacturing(), 'Fremdfertigung')
        self.assertEqual(external.type_setting_key(), 'BUILD_TYPE')
        external.settings_lookup = {**SETTINGS, 'EXTERNAL_BUILD_TYPE': 'Fremdfertigung'}.get
        self.assertEqual(external.type_setting_key(), 'EXTERNAL_BUILD_TYPE')


class HelperTest(unittest.TestCase):
    def test_iso_duration(self):
        self.assertEqual(iso_duration(7), 'PT7H')
        self.assertEqual(iso_duration(2.5), 'PT2H30M')
        self.assertEqual(iso_duration(0.25), 'PT0H15M')

    def test_option_links(self):
        spec = {'_links': {'allowedValues': [
            {'href': '/api/v3/custom_options/1', 'title': 'Drehen Knuth'},
            {'href': '/api/v3/custom_options/2', 'title': 'Entgraten'},
        ]}}
        self.assertEqual(option_links(spec, 'drehen knuth', False), ({'href': '/api/v3/custom_options/1'}, []))
        self.assertEqual(
            option_links(spec, 'Drehen Knuth, Entgraten, Laser', True),
            ([{'href': '/api/v3/custom_options/1'}, {'href': '/api/v3/custom_options/2'}], ['Laser']),
        )
        self.assertEqual(option_links(spec, '', False), ({'href': None}, []))

    def test_option_links_embedded(self):
        spec = {'_embedded': {'allowedValues': [{'value': 'Fräsen', '_links': {'self': {'href': '/o/5'}}}]}}
        self.assertEqual(option_links(spec, 'Fräsen', False), ({'href': '/o/5'}, []))


if __name__ == '__main__':
    unittest.main()
