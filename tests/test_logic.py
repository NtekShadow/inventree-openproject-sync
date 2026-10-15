"""Tests for the Django independent parts of the plugin.

Run with: python -m unittest discover -s tests
"""

import unittest

from openproject_sync import refs
from openproject_sync.client import OpenProjectError
from openproject_sync.sync import (
    MARKER,
    compose_description,
    match_principal,
    parse_mapping,
    plan_two_way,
    render_block,
    split_description,
)

BASE = 'https://pm.example.com'


class FindRefsTest(unittest.TestCase):
    def keys(self, text, prefix=refs.DEFAULT_PREFIX):
        return [r.key for r in refs.find_refs(text, prefix)]

    def test_semantic_and_numeric(self):
        self.assertEqual(self.keys('Teil für OP#EL-05 und OP#1234.'), ['EL-5', '1234'])

    def test_case_and_duplicates(self):
        self.assertEqual(self.keys('op#el-5, OP#EL-05, OP#ROS2-89'), ['EL-5', 'ROS2-89'])

    def test_no_partial_matches(self):
        self.assertEqual(self.keys('TOP#EL-5 OP#EL- OP# OP#EL-5x /OP#EL-5'), [])

    def test_inside_markdown_link_is_found(self):
        self.assertEqual(self.keys('[OP#EL-5](https://x/work_packages/EL-5)'), ['EL-5'])

    def test_custom_prefix(self):
        self.assertEqual(self.keys('siehe WP:EL-7', prefix='WP:'), ['EL-7'])

    def test_url_ref(self):
        ref = refs.find_url_ref(f'{BASE}/projects/el/work_packages/EL-12/activity', BASE)
        self.assertEqual(ref.key, 'EL-12')
        self.assertEqual(refs.find_url_ref(f'{BASE}/wp/77', BASE).key, '77')
        self.assertIsNone(refs.find_url_ref('https://other.com/work_packages/12', BASE))


class ProjectKeyTest(unittest.TestCase):
    def test_identifier_and_url(self):
        self.assertEqual(refs.project_key('FER_27'), 'FER_27')
        self.assertEqual(refs.project_key('https://pm.example.com/projects/FER_27?jump=home'), 'FER_27')
        self.assertEqual(refs.project_key(f'{BASE}/projects/fer_27/work_packages'), 'fer_27')
        self.assertEqual(refs.project_key(''), '')


class LinkifyTest(unittest.TestCase):
    def test_links_bare_refs(self):
        self.assertEqual(
            refs.linkify_markdown('Siehe OP#EL-05.', BASE),
            f'Siehe [OP#EL-05]({BASE}/work_packages/EL-5).',
        )

    def test_idempotent(self):
        once = refs.linkify_markdown('OP#EL-5 und OP#12', BASE)
        self.assertEqual(refs.linkify_markdown(once, BASE), once)

    def test_existing_link_text_and_code_untouched(self):
        text = '[siehe OP#EL-5](https://x) `OP#EL-6`\n```\nOP#EL-7\n```'
        self.assertEqual(refs.linkify_markdown(text, BASE), text)

    def test_no_base_url(self):
        self.assertEqual(refs.linkify_markdown('OP#EL-5', ''), 'OP#EL-5')


class PlanDateTest(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(plan_two_way('2026-01-01', '2026-01-01', None, True, False), (None, '2026-01-01'))

    def test_first_sync_prefers_inventree(self):
        self.assertEqual(plan_two_way('2026-01-01', '2026-02-01', None, True, False), ('push', '2026-01-01'))
        self.assertEqual(plan_two_way(None, '2026-02-01', None, True, False), ('pull', '2026-02-01'))

    def test_changed_in_inventree(self):
        self.assertEqual(plan_two_way('2026-03-01', '2026-01-01', '2026-01-01', True, True), ('push', '2026-03-01'))

    def test_changed_in_openproject(self):
        self.assertEqual(plan_two_way('2026-01-01', '2026-03-01', '2026-01-01', True, True), ('pull', '2026-03-01'))

    def test_conflict_openproject_wins(self):
        self.assertEqual(plan_two_way('2026-02-01', '2026-03-01', '2026-01-01', True, True), ('pull', '2026-03-01'))

    def test_pull_disabled(self):
        self.assertEqual(plan_two_way('2026-02-01', '2026-03-01', '2026-02-01', False, True), ('push', '2026-02-01'))


class DescriptionTest(unittest.TestCase):
    def test_roundtrip_keeps_user_text(self):
        block = render_block([('Bestellung', '[PO-1](x)'), ('Lieferant', 'A|B')])
        self.assertIn('A/B', block)
        desc = compose_description('Eigener Text', block)
        self.assertEqual(split_description(desc), 'Eigener Text')
        new = compose_description(split_description(desc), render_block([('Bestellung', 'PO-1')]))
        self.assertEqual(new.count(MARKER), 1)
        self.assertTrue(new.startswith('Eigener Text'))

    def test_empty_user_part(self):
        block = render_block([])
        self.assertEqual(compose_description(split_description(block), block), block)


class MiscTest(unittest.TestCase):
    def test_parse_mapping(self):
        self.assertEqual(
            parse_mapping('PENDING=Neu; COMPLETE = Abgeschlossen\nX=\n=y; junk'),
            {'PENDING': 'Neu', 'COMPLETE': 'Abgeschlossen', 'X': ''},
        )

    def test_invalid_attributes(self):
        single = OpenProjectError('x', 422, {'_embedded': {'details': {'attribute': 'startDate'}}})
        self.assertEqual(single.invalid_attributes(), {'startDate'})
        multi = OpenProjectError('x', 422, {'_embedded': {'errors': [
            {'_embedded': {'details': {'attribute': 'status'}}},
            {'_embedded': {'details': {'attribute': 'percentageDone'}}},
        ]}})
        self.assertEqual(multi.invalid_attributes(), {'status', 'percentageDone'})



class MatchPrincipalTest(unittest.TestCase):
    PRINCIPALS = [
        {'_type': 'User', 'name': 'Anna Dreher', 'login': 'adreher'},
        {'_type': 'User', 'name': 'Bob Fräser', 'login': 'bob@example.com'},
        {'_type': 'User', 'name': 'Carl', 'email': 'carl@example.com', 'login': 'carl'},
        {'_type': 'Group', 'name': 'Fertigung'},
    ]

    def match(self, **who):
        found = match_principal(self.PRINCIPALS, who)
        return found and found['name']

    def test_user(self):
        self.assertEqual(self.match(type='user', email='CARL@example.com', login='x', name='y'), 'Carl')
        self.assertEqual(self.match(type='user', email='bob@example.com', login='bob', name=''), 'Bob Fräser')
        self.assertEqual(self.match(type='user', email='', login='ADreher', name=''), 'Anna Dreher')
        self.assertEqual(self.match(type='user', email='', login='anna', name='Anna Dreher'), 'Anna Dreher')
        self.assertIsNone(self.match(type='user', email='', login='', name='Fertigung'))

    def test_group(self):
        self.assertEqual(self.match(type='group', name='fertigung'), 'Fertigung')
        self.assertIsNone(self.match(type='group', name='Anna Dreher'))


if __name__ == '__main__':
    unittest.main()
