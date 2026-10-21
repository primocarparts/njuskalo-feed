import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import PrimoNjusaklo_DAILY_STOCK_SYNC as sync

NEW_TEMPLATE = """<p>RENAULT TWINGO 55 PIN ECU / 21656026-0 / 8200023519</p>
<p>Original used engine control unit (ECU) from Primo Car Parts. Photos show the actual used item offered for sale.</p>
<h3>Reference Numbers</h3><p>21656026-0</p><p>8200023519</p>
<h3>Important Compatibility Check</h3>
<p>Before ordering, compare the manufacturer and OEM reference numbers, connectors and casing to your original part. Vehicle make, model or engine alone is not enough to confirm interchangeability.</p>
<h3>Condition</h3><p>Used original part. Normal cosmetic wear may be present. Inspect the actual item photos before purchase.</p>
<h3>Programming / Immobilizer</h3>
<p>Depending on the vehicle, installing a used ECU may require coding, cloning, immobilizer synchronization or data transfer from the original ECU.</p>"""


class CroatianTranslationTests(unittest.TestCase):
    def test_new_shopify_template_fully_croatian(self):
        with patch.dict(os.environ, {'DEEPL_API_KEY': ''}):
            result = sync.cro_desc(NEW_TEMPLATE)
        self.assertIn('Originalni rabljeni kompjuter motora', result)
        self.assertIn('Prije narudžbe usporedite', result)
        self.assertIn('Ovisno o vozilu', result)
        self.assertIn('DOSTAVA ZA HRVATSKU', result)
        self.assertIn('21656026-0', result)
        self.assertIn('8200023519', result)
        for english in ['Original used', 'Before ordering', 'Used original part',
                        'Depending on the vehicle', 'Reference Numbers',
                        'Normal cosmetic wear', 'engine control unit (ECU) from']:
            self.assertNotIn(english.lower(), result.lower())

    def test_old_template_and_shipping_not_doubled(self):
        src = ('<p>Original used engine control unit (ECU).</p>'
               '<p>Used original part. The item may show normal signs of previous use. '
               'Please check all product photos carefully, as they show the actual item you will receive.</p>'
               '<h3>Worldwide Shipping</h3><p>Shipping 12 EUR</p>')
        with patch.dict(os.environ, {'DEEPL_API_KEY': ''}):
            result = sync.cro_desc(src)
        self.assertIn('Rabljeni originalni dio.', result)
        self.assertNotIn('Worldwide Shipping', result)
        self.assertNotIn('Shipping 12', result)
        self.assertEqual(1, result.count('DOSTAVA ZA HRVATSKU'))

    def test_title_rule_preserves_ecu_and_part_numbers(self):
        ecu = 'RENAULT TWINGO 55 PIN ECU / 21656026-0 / 8200023519'
        self.assertEqual(ecu, sync.title_for_njuskalo(ecu))
        self.assertEqual('BMW FILTER ZRAKA / 123456789',
                         sync.title_for_njuskalo('BMW AIR FILTER / 123456789'))
        self.assertEqual('OPEL ABS PUMPA 0265950001',
                         sync.title_for_njuskalo('OPEL ABS PUMP 0265950001'))

    def test_existing_description_update_and_stock_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / 'njuskalo.xml'
            xml.write_text(
                '<ad_list>'
                '<ad_item><original_id>B01-01</original_id><title>ECU TITLE</title>'
                '<description>Original used engine control unit (ECU).</description></ad_item>'
                '<ad_item><original_id>B01-02</original_id><title>SOLD ECU</title></ad_item>'
                '</ad_list>', encoding='utf-8')
            old_p = {'title': 'ECU TITLE', 'descriptionHtml': NEW_TEMPLATE,
                     'images': {'nodes': []}}
            new_p = {'title': 'NEW ECU', 'descriptionHtml': '<p>Original used engine control unit (ECU).</p>',
                     'images': {'nodes': []}}
            active = {'B01-01': (old_p, {'sku': 'B01-01', 'price': '30', 'inventoryQuantity': 1}),
                      'B01-03': (new_p, {'sku': 'B01-03', 'price': '45', 'inventoryQuantity': 1})}
            with (patch.object(sync, 'OUT', xml),
                  patch.object(sync, 'auth', return_value='test'),
                  patch.object(sync, 'shopify_products', return_value=active),
                  patch.dict(os.environ, {'DEEPL_API_KEY': ''})):
                sync.main()
            root = ET.parse(xml).getroot()
            ids = {x.findtext('original_id'): x for x in root.findall('ad_item')}
            self.assertEqual({'B01-01', 'B01-03'}, set(ids))
            self.assertIn('Prije narudžbe usporedite', ids['B01-01'].findtext('description'))
            self.assertIn('Originalni rabljeni kompjuter', ids['B01-03'].findtext('description'))


if __name__ == '__main__':
    unittest.main()
