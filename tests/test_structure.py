"""Assemblies become parents, process stages become predecessors."""

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest import mock

from openproject_sync.orders import BuildAdapter

SETTINGS = {'PROCESS_PARAMETER': 'Fertigungsverfahren', 'MACHINE_PARAMETER': 'Maschine'}


class Children(list):
    def all(self):
        return list(self)


def make_build(name, process='', parent=None):
    params = [SimpleNamespace(template=SimpleNamespace(name='Fertigungsverfahren'), data=process)] if process else []
    part = SimpleNamespace(full_name=name, parameters=lambda: params, manufacturing_costs=None)
    build = SimpleNamespace(reference=name, part=part, parent=parent, children=Children(), parameters=lambda: [])
    if parent is not None:
        parent.children.append(build)
    return build


def adapter(build):
    a = BuildAdapter(build)
    a.settings_lookup = SETTINGS.get
    return a


class StructureTest(unittest.TestCase):
    def setUp(self):
        # Akku (assembly) <- anodized <- milled <- turned ; Akku <- Modul (assembly) <- Zelle
        self.akku = make_build('Akku')
        self.anodized = make_build('anodized', 'Eloxieren', self.akku)
        self.milled = make_build('milled', 'Fräsen', self.anodized)
        self.turned = make_build('turned', 'Drehen', self.milled)
        self.modul = make_build('Modul', '', self.akku)
        self.zelle = make_build('Zelle', '', self.modul)

        # preceding_orders() looks up purchase orders; no database here
        self.purchase_orders = []
        order_models = ModuleType('order.models')
        order_models.PurchaseOrder = SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **kw: SimpleNamespace(distinct=lambda: self.purchase_orders)
            )
        )
        patcher = mock.patch.dict(sys.modules, {'order': ModuleType('order'), 'order.models': order_models})
        patcher.start()
        self.addCleanup(patcher.stop)

    def refs(self, builds):
        return [b.reference for b in builds]

    def test_stages_are_children_of_the_assembly(self):
        for stage in (self.anodized, self.milled, self.turned):
            self.assertIs(adapter(stage).parent_order(), self.akku)
        self.assertIs(adapter(self.modul).parent_order(), self.akku)
        self.assertIs(adapter(self.zelle).parent_order(), self.modul)
        self.assertIsNone(adapter(self.akku).parent_order())

    def test_child_orders(self):
        self.assertEqual(self.refs(adapter(self.akku).child_orders()), ['anodized', 'Modul', 'milled', 'turned'])
        self.assertEqual(self.refs(adapter(self.modul).child_orders()), ['Zelle'])
        self.assertEqual(adapter(self.milled).child_orders(), [])

    def test_stage_chain(self):
        self.assertEqual(self.refs(adapter(self.turned).following_orders()), ['milled'])
        self.assertEqual(self.refs(adapter(self.milled).following_orders()), ['anodized'])
        # The assembly is the parent, not the successor
        self.assertEqual(adapter(self.anodized).following_orders(), [])
        self.assertEqual(adapter(self.modul).following_orders(), [])
        self.assertEqual(self.refs(adapter(self.milled).preceding_orders()), ['turned'])
        self.assertEqual(adapter(self.akku).preceding_orders(), [])

    def test_supplier_of_external_build(self):
        po = SimpleNamespace(supplier=SimpleNamespace(name='Eloxal GmbH'))
        self.purchase_orders = [po, po]
        self.assertEqual(adapter(self.anodized).supplier(), '')
        self.anodized.external = True
        self.assertEqual(adapter(self.anodized).supplier(), 'Eloxal GmbH')


if __name__ == '__main__':
    unittest.main()
