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


if __name__ == '__main__':
    unittest.main()
