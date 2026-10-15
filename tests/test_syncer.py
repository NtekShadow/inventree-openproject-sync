"""Sync engine tests with an in-memory OpenProject and fake orders (no Django)."""

import copy
import unittest
from unittest import mock

from openproject_sync import sync as sync_module
from openproject_sync.client import OpenProjectError
from openproject_sync.orders import OrderAdapter
from openproject_sync.sync import MARKER, Syncer

SETTINGS = {
    'OPENPROJECT_URL': 'https://pm.example.com',
    'OPENPROJECT_API_KEY': 'key',
    'SYNC_BUILD_ORDERS': True,
    'AUTO_CREATE': True,
    'PROJECT': 'FER_27',
    'BUILD_TYPE': 'Fertigung',
    'STATUS_MAP': 'PENDING=Neu; PRODUCTION=In Bearbeitung',
    'PUSH_PROGRESS': True,
    'PULL_DATES': True,
    'CUSTOM_FIELDS': 'Referenz=reference; Maschine=machine',
    'PUSH_WORK': True,
    'REFERENCE_PREFIX': 'OP#',
}


class FakePlugin:
    def __init__(self, **overrides):
        self.settings = {**SETTINGS, **overrides}

    def get_setting(self, key):
        return self.settings.get(key)


class FakeOpenProject:
    """Just enough of the API for the syncer."""

    def __init__(self):
        self.wps = {
            100: self._wp(100, 'EL-5', 'Querlenker vorne', project=7),
        }
        self.next_id = 200
        self.reject = set()  # attributes answered with 422
        self.patches = []
        self.relations = []

    @staticmethod
    def _wp(wp_id, display, subject, project=7, type_id=1, **extra):
        return {
            'id': wp_id,
            'displayId': display,
            'subject': subject,
            'lockVersion': 0,
            'description': {'raw': ''},
            'startDate': None,
            'dueDate': None,
            '_links': {
                'project': {'href': f'/api/v3/projects/{project}', 'title': 'Elektrik'},
                'type': {'href': f'/api/v3/types/{type_id}', 'title': 'Aufgabe'},
                'status': {'href': '/api/v3/statuses/1', 'title': 'Neu'},
            },
            **extra,
        }

    def _check(self, payload):
        bad = [a for a in self.reject if a in payload or a in payload.get('_links', {})]
        if bad:
            raise OpenProjectError('invalid', 422, {'_embedded': {'details': {'attribute': bad[0]}}})

    def get_work_package(self, key):
        key = str(key)
        for wp in self.wps.values():
            if str(wp['id']) == key or wp['displayId'] == key:
                return copy.deepcopy(wp)
        return None

    def create_work_package(self, payload):
        self._check(payload)
        self._titles(payload['_links'])
        wp_id = self.next_id
        self.next_id += 1
        project = payload['_links']['project']['href'].rsplit('/', 1)[-1]
        wp = self._wp(wp_id, f'EL-{wp_id}', payload['subject'], project=project)
        wp.update({k: v for k, v in payload.items() if k != '_links'})
        wp['_links'].update({k: v for k, v in payload['_links'].items() if k != 'project'})
        self.wps[wp_id] = wp
        return copy.deepcopy(wp)

    def _titles(self, links):
        # The real API returns list options with their title
        options = {'/api/v3/custom_options/51': 'Knuth', '/api/v3/custom_options/52': 'Hermle'}
        for value in links.values():
            if isinstance(value, dict) and value.get('href') in options:
                value['title'] = options[value['href']]

    def update_work_package(self, wp_id, payload):
        self._check(payload)
        self._titles(payload.get('_links', {}))
        wp = self.wps[wp_id]
        if payload['lockVersion'] != wp['lockVersion']:
            raise OpenProjectError('conflict', 409)
        self.patches.append(payload)
        wp.update({k: v for k, v in payload.items() if k not in ('_links', 'lockVersion')})
        wp['_links'].update(payload.get('_links', {}))
        wp['lockVersion'] += 1
        return copy.deepcopy(wp)

    def get_project(self, key):
        return {'ADM': {'id': 9}, 'FER_27': {'id': 37}}.get(key)

    def create_relation(self, from_id, to_id, rel_type):
        if (from_id, to_id, rel_type) in self.relations:
            raise OpenProjectError('exists', 422)
        self.relations.append((from_id, to_id, rel_type))
        return {}

    def project_types(self, project_id):
        return [{'id': 2, 'name': 'Meilenstein', 'isMilestone': True}, {'id': 1, 'name': 'Aufgabe'}]

    def status_by_name(self, name):
        ids = {'Neu': 1, 'In Bearbeitung': 7}
        if name not in ids:
            return None
        return {'name': name, '_links': {'self': {'href': f'/api/v3/statuses/{ids[name]}'}}}

    def schema(self, project_id, type_id):
        return {
            'customField3': {'name': 'Referenz', 'type': 'String'},
            'customField5': {'name': 'Maschine', 'type': 'CustomOption', '_links': {'allowedValues': [
                {'href': '/api/v3/custom_options/51', 'title': 'Knuth'},
                {'href': '/api/v3/custom_options/52', 'title': 'Hermle'},
            ]}},
        }


class FakeInstance:
    """Stands in for a Build: metadata, fields and save()."""

    saves = 0

    def __init__(self, **fields):
        self.pk = 1
        self.metadata = {}
        self.reference = 'BO-0001'
        self.title = ''
        self.notes = ''
        self.link = ''
        self.status = 'PENDING'
        self.start_date = None
        self.target_date = None
        self.__dict__.update(fields)

    def get_metadata(self, key):
        return (self.metadata or {}).get(key)

    def set_metadata(self, key, data, commit=True):
        self.metadata[key] = data

    def save(self):
        FakeInstance.saves += 1

    class objects:  # noqa: N801 - mimics a Django manager
        @staticmethod
        def filter(**kwargs):
            return mock.Mock(update=lambda **kw: None)


class FakeAdapter(OrderAdapter):
    model_type = 'build'
    label = 'Fertigungsauftrag'
    type_setting = 'BUILD_TYPE'
    sync_setting = 'SYNC_BUILD_ORDERS'
    done = 0
    hours = None
    machine = ''
    parent = None
    children = []
    before = []
    after = []

    def web_url(self):
        return 'https://inventree.example.com/web/manufacturing/build-order/1'

    def status_name(self):
        return self.instance.status

    def status_text(self):
        return self.instance.status.title()

    def is_active(self):
        return True

    def subject(self):
        return f'{self.reference}: {self.instance.title or "Halter"} (10 Stk)'

    def progress(self):
        return self.done

    def info_rows(self):
        return [('Fertigungsauftrag', self.reference), ('Menge', f'{self.done}%')]

    def work_hours(self):
        return self.hours

    def parent_order(self):
        return self.parent

    def child_orders(self):
        return self.children

    def set_machine(self, value):
        self.machine = value

    def field_values(self):
        return {**super().field_values(), 'machine': self.machine}

    def preceding_orders(self):
        return self.before

    def following_orders(self):
        return self.after


class SyncerTest(unittest.TestCase):
    def setUp(self):
        self.op = FakeOpenProject()
        self.syncer = Syncer(FakePlugin())
        self.syncer._client = self.op
        patcher = mock.patch.object(Syncer, '_linked_order', return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def adapter(self, **fields):
        return FakeAdapter(FakeInstance(**fields))

    def md(self, adapter):
        return adapter.instance.metadata[sync_module.META_KEY]

    def test_no_auto_create(self):
        for settings in ({'AUTO_CREATE': False}, {'PROJECT': ''}):
            syncer = Syncer(FakePlugin(**settings))
            syncer._client = self.op
            self.assertEqual(syncer.sync(self.adapter()), 'nicht verknüpft')
        self.assertEqual(len(self.op.wps), 1)

    def test_create_in_project_with_reference_as_relation(self):
        adapter = self.adapter(notes='Für OP#EL-05', target_date=None)
        self.assertIn('angelegt', self.syncer.sync(adapter))
        wp = self.op.wps[200]
        self.assertNotIn('parent', wp['_links'])
        self.assertEqual(wp['_links']['project']['href'], '/api/v3/projects/37')
        self.assertEqual(self.op.relations, [(200, 100, 'relates')])
        # 'Fertigung' type does not exist -> first non milestone type
        self.assertEqual(wp['_links']['type']['href'], '/api/v3/types/1')
        self.assertEqual(wp['_links']['status']['href'], '/api/v3/statuses/1')
        self.assertEqual(wp['customField3'], 'BO-0001')
        self.assertIn(MARKER, wp['description']['raw'])
        self.assertEqual(self.md(adapter)['wp'], 200)
        self.assertEqual(adapter.instance.link, 'https://pm.example.com/work_packages/EL-200')

    def test_sub_build_precedes_parent_build(self):
        adapter = self.adapter()
        adapter.after = [FakeInstance(metadata={'openproject': {'wp': 100}})]
        self.syncer.sync(adapter)
        wp = self.op.wps[200]
        self.assertNotIn('parent', wp['_links'])
        self.assertEqual(wp['_links']['project']['href'], '/api/v3/projects/37')
        self.assertEqual(self.op.relations, [(200, 100, 'precedes')])

    def test_previous_step_precedes_this_build(self):
        adapter = self.adapter()
        adapter.before = [FakeInstance(metadata={'openproject': {'wp': 100}})]
        self.syncer.sync(adapter)
        self.assertEqual(self.op.relations, [(100, 200, 'precedes')])

    def test_project_override_accepts_url(self):
        adapter = self.adapter()
        self.syncer.create(adapter, project='https://pm.example.com/projects/ADM?jump=home')
        self.assertEqual(self.op.wps[200]['_links']['project']['href'], '/api/v3/projects/9')
        self.assertNotIn('parent', self.op.wps[200]['_links'])

    def test_link_field_links_existing(self):
        adapter = self.adapter(link='https://pm.example.com/projects/el/work_packages/EL-5')
        self.assertIn('verknüpft', self.syncer.sync(adapter))
        wp = self.op.wps[100]
        # Subject of an existing work package is kept, the InvenTree block is added
        self.assertEqual(wp['subject'], 'Querlenker vorne')
        self.assertIn(MARKER, wp['description']['raw'])

    def test_second_sync_is_noop_and_loop_free(self):
        adapter = self.adapter(notes='OP#EL-5', target_date='2026-11-01')
        self.syncer.sync(adapter)
        patches = len(self.op.patches)
        self.assertEqual(self.syncer.sync(adapter), 'keine Änderungen')
        self.assertEqual(len(self.op.patches), patches)

    def test_push_changes_from_inventree(self):
        adapter = self.adapter(notes='OP#EL-5')
        self.syncer.sync(adapter)
        adapter.instance.status = 'PRODUCTION'
        adapter.done = 40
        result = self.syncer.sync(adapter)
        self.assertIn('status', result)
        wp = self.op.wps[200]
        self.assertEqual(wp['_links']['status']['href'], '/api/v3/statuses/7')
        self.assertEqual(wp['percentageDone'], 40)

    def test_manual_subject_edit_in_openproject_survives(self):
        adapter = self.adapter(notes='OP#EL-5')
        self.syncer.sync(adapter)
        self.op.wps[200]['subject'] = 'Von Hand umbenannt'
        self.op.wps[200]['lockVersion'] += 1
        self.syncer.sync(adapter)
        self.assertEqual(self.op.wps[200]['subject'], 'Von Hand umbenannt')

    def test_user_text_in_description_is_kept(self):
        adapter = self.adapter(notes='OP#EL-5')
        self.syncer.sync(adapter)
        wp = self.op.wps[200]
        wp['description']['raw'] = 'Bitte eloxieren.\n\n' + wp['description']['raw']
        adapter.done = 50
        self.syncer.sync(adapter)
        raw = wp['description']['raw']
        self.assertTrue(raw.startswith('Bitte eloxieren.'))
        self.assertIn('50%', raw)
        self.assertEqual(raw.count(MARKER), 1)

    def test_dates_pulled_from_openproject(self):
        adapter = self.adapter(notes='OP#EL-5', target_date='2026-11-01')
        self.syncer.sync(adapter)
        self.assertEqual(self.op.wps[200]['dueDate'], '2026-11-01')
        self.op.wps[200]['dueDate'] = '2026-11-20'
        self.op.wps[200]['lockVersion'] += 1
        saves = FakeInstance.saves
        self.assertIn('übernommen', self.syncer.sync(adapter))
        self.assertEqual(str(adapter.instance.target_date), '2026-11-20')
        self.assertEqual(FakeInstance.saves, saves + 1)
        # The save event that follows finds nothing to do
        self.assertEqual(self.syncer.sync(adapter), 'keine Änderungen')

    def test_dates_pushed_from_inventree(self):
        adapter = self.adapter(notes='OP#EL-5', target_date='2026-11-01')
        self.syncer.sync(adapter)
        adapter.instance.target_date = '2026-12-24'
        self.syncer.sync(adapter)
        self.assertEqual(self.op.wps[200]['dueDate'], '2026-12-24')

    def test_rejected_field_is_dropped_with_warning(self):
        self.op.reject = {'percentageDone'}
        adapter = self.adapter(notes='OP#EL-5')
        adapter.done = 10
        self.syncer.sync(adapter)
        self.assertIn(200, self.op.wps)
        self.assertTrue(self.md(adapter)['warnings'])
        # Not retried while the value stays the same
        self.assertEqual(self.syncer.sync(adapter), 'keine Änderungen')

    def test_deleted_work_package_records_error(self):
        adapter = self.adapter(notes='OP#EL-5')
        self.syncer.sync(adapter)
        del self.op.wps[200]
        with self.assertRaises(sync_module.SyncError):
            self.syncer.sync(adapter)
        self.assertIn('nicht gefunden', self.md(adapter)['error'])

    def test_legacy_link_is_migrated(self):
        legacy = sync_module.LEGACY_MARKERS[0]
        self.op.wps[100]['description']['raw'] = f'Notiz\n\n{legacy}\n\n| alt | alt |'
        adapter = self.adapter(metadata={'openproject': {'work_package_id': 100, 'synced_op_activity_ids': [1]}})
        self.syncer.sync(adapter)
        raw = self.op.wps[100]['description']['raw']
        self.assertTrue(raw.startswith('Notiz'))
        self.assertNotIn('alt', raw)
        self.assertIn(MARKER, raw)
        self.assertEqual(self.md(adapter)['wp'], 100)
        self.assertNotIn('synced_op_activity_ids', self.md(adapter))

    def test_purchase_order_precedes_build(self):
        adapter = self.adapter()
        adapter.after = [FakeInstance(metadata={'openproject': {'wp': 100}})]
        self.syncer.sync(adapter)
        self.assertEqual(self.op.relations, [(200, 100, 'precedes')])
        # Remembered, not created again; a later new reference is added
        adapter.instance.notes = 'OP#EL-5'
        self.syncer.sync(adapter)
        self.assertEqual(self.op.relations, [(200, 100, 'precedes'), (200, 100, 'relates')])

    def test_relation_existing_in_openproject_is_ok(self):
        self.op.relations.append((200, 100, 'relates'))
        adapter = self.adapter(notes='OP#EL-5')
        self.syncer.sync(adapter)
        self.assertEqual(self.md(adapter)['relations'], ['relates:EL-5'])

    def test_move_to_other_project(self):
        adapter = self.adapter()
        self.syncer.sync(adapter)
        self.assertIn('verschoben', self.syncer.move(adapter, 'https://pm.example.com/projects/ADM'))
        self.assertEqual(self.op.wps[200]['_links']['project']['href'], '/api/v3/projects/9')
        self.assertEqual(self.syncer.move(adapter, 'ADM'), 'bereits in diesem Projekt')

    def test_work_and_machine_list_field(self):
        adapter = self.adapter()
        adapter.hours, adapter.machine = 2.5, 'Knuth'
        self.syncer.sync(adapter)
        wp = self.op.wps[200]
        self.assertEqual(wp['estimatedTime'], 'PT2H30M')
        self.assertEqual(wp['_links']['customField5']['href'], '/api/v3/custom_options/51')
        adapter.machine = 'Weiler'
        self.syncer.sync(adapter)
        self.assertIn('Weiler', ' '.join(self.md(adapter)['warnings']))

    def test_machine_changed_in_openproject_is_pulled(self):
        adapter = self.adapter()
        adapter.machine = 'Knuth'
        self.syncer.sync(adapter)
        # Planner moves the card to the Hermle column
        self.op.wps[200]['_links']['customField5'] = {'href': '/api/v3/custom_options/52', 'title': 'Hermle'}
        self.op.wps[200]['lockVersion'] += 1
        self.assertIn('Maschine', self.syncer.sync(adapter))
        self.assertEqual(adapter.machine, 'Hermle')
        self.assertEqual(self.syncer.sync(adapter), 'keine Änderungen')
        # A later change in InvenTree goes to OpenProject again
        adapter.machine = 'Knuth'
        self.syncer.sync(adapter)
        self.assertEqual(self.op.wps[200]['_links']['customField5']['title'], 'Knuth')

    def test_parent_set_from_assembly(self):
        adapter = self.adapter()
        adapter.parent = FakeInstance(metadata={'openproject': {'wp': 100}})
        self.syncer.sync(adapter)
        self.assertEqual(self.op.wps[200]['_links']['parent']['href'], '/api/v3/work_packages/100')
        self.assertEqual(self.syncer.sync(adapter), 'keine Änderungen')

    def test_assembly_created_later_adopts_children(self):
        child = self.adapter()
        self.syncer.sync(child)  # WP 200 without parent
        self.assertNotIn('parent', self.op.wps[200]['_links'])
        assembly = self.adapter(reference='BO-0002')
        assembly.children = [child.instance]
        with mock.patch.object(sync_module, 'adapter_for', return_value=child):
            self.syncer.sync(assembly)  # WP 201
        self.assertEqual(self.op.wps[200]['_links']['parent']['href'], '/api/v3/work_packages/201')
        # The child's own sync now agrees with OpenProject
        child.parent = assembly.instance
        self.assertEqual(self.syncer.sync(child), 'keine Änderungen')

    def test_unlink(self):
        adapter = self.adapter(notes='OP#EL-5')
        self.syncer.sync(adapter)
        self.syncer.unlink(adapter)
        self.assertNotIn(sync_module.META_KEY, adapter.instance.metadata)


if __name__ == '__main__':
    unittest.main()
