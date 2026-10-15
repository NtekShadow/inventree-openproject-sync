"""Purchase order values."""

import unittest
from decimal import Decimal
from types import SimpleNamespace

from openproject_sync.orders import PurchaseOrderAdapter


def adapter(total_price):
    po = SimpleNamespace(reference='PO-0001', total_price=total_price, supplier=None, supplier_reference='')
    return PurchaseOrderAdapter(po)


class PriceTest(unittest.TestCase):
    def test_price(self):
        a = adapter(SimpleNamespace(amount=Decimal('1234.5'), currency='EUR'))
        self.assertEqual(a.price(), (1234.5, 'EUR'))
        self.assertEqual(a.price_text(), '1.234,50 EUR')

    def test_no_price(self):
        for total in (None, SimpleNamespace(amount=Decimal(0), currency='EUR')):
            a = adapter(total)
            self.assertEqual(a.price(), (None, ''))
            self.assertEqual(a.price_text(), '')


def line(qty, name, sku='', received=0):
    return SimpleNamespace(
        quantity=qty, received=received, part=SimpleNamespace(part=SimpleNamespace(full_name=name), SKU=sku)
    )


def subject_adapter(description='', supplier='Würth', lines=()):
    po = SimpleNamespace(
        reference='PO-0019',
        description=description,
        supplier=SimpleNamespace(name=supplier) if supplier else None,
        lines=SimpleNamespace(all=lambda: list(lines)),
    )
    return PurchaseOrderAdapter(po)


class SubjectTest(unittest.TestCase):
    def test_description_with_supplier(self):
        a = subject_adapter('sponsoring anfrage 1')
        self.assertEqual(a.subject(), 'PO-0019 bei Würth: sponsoring anfrage 1')

    def test_supplier_without_description(self):
        self.assertEqual(subject_adapter('  ').subject(), 'PO-0019 bei Würth')


class DetailsTest(unittest.TestCase):
    def test_line_items_table(self):
        lines = [line(100, 'Schraube M3', '0084 3 10', received=40), line(5, 'Mutter M3')]
        self.assertEqual(
            subject_adapter(lines=lines).details_markdown(),
            '| Teil | Lieferanten-Nr. | Menge | Geliefert |\n'
            '|---|---|---|---|\n'
            '| Schraube M3 | 0084 3 10 | 100 | 40 |\n'
            '| Mutter M3 | - | 5 | 0 |',
        )

    def test_no_lines(self):
        self.assertEqual(subject_adapter().details_markdown(), '')


if __name__ == '__main__':
    unittest.main()
