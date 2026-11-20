import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import PrimoNjusaklo_DAILY_STOCK_SYNC as sync


def item(sku, price, created="2026-11-01T00:00:00Z",
         title="ECU", stock=1, tags=None, product_type=""):
    product = {
        "title": title, "createdAt": created, "tags": tags or [],
        "productType": product_type, "descriptionHtml": "",
        "images": {"nodes": []},
    }
    variant = {"sku": sku, "price": str(price), "inventoryQuantity": stock}
    return product, variant


class FeedSelectionTests(unittest.TestCase):
    def test_removes_bulk_and_sold_items(self):
        current = {
            "B01-01": item("B01-01", 39),
            "B01-02": item("B01-02", 40, stock=0),
            "PCP-BULK-123": item("PCP-BULK-123", 50),
            "PCP-REF-123": item("PCP-REF-123", 60),
            "B01-03": item("B01-03", 65, title="3x ECU BOSCH"),
            "B01-04": item("B01-04", 70, title="10x ECU BOSCH"),
            "B01-05": item("B01-05", 80, tags=["Bulk Orders"]),
            "B01-06": item("B01-06", 90, product_type="BULK ECU"),
            "B01-07": item("B01-07", 20, title="ABS PUMP"),
        }
        selected, sold, bulk, over_limit = sync.select_feed_products(current)
        self.assertEqual({"B01-01", "B01-07"}, set(selected))
        self.assertEqual((1, 6, 0), (sold, bulk, over_limit))

    def test_above_limit_drops_lowest_price_and_uses_newest_tiebreak(self):
        current = {
            "cheap": item("cheap", 5, "2026-11-20T10:00:00Z"),
            "high": item("high", 90, "2026-11-01T00:00:00Z"),
            "old": item("old", 50, "2026-11-03T00:00:00Z"),
            "new": item("new", 50, "2026-11-19T00:00:00Z"),
        }
        with patch.object(sync, "MAX_ADS", 2):
            selected, sold, bulk, over_limit = sync.select_feed_products(current)
        self.assertEqual({"high", "new"}, set(selected))
        self.assertEqual(2, over_limit)

    def test_when_under_limit_keep_all_prices(self):
        with patch.object(sync, "MAX_ADS", 3):
            selected, *_ = sync.select_feed_products({
                "A": item("A", "5"),
                "B": item("B", "100"),
            })
        self.assertEqual({"A", "B"}, set(selected))

    def test_empty_shopify_data_does_not_delete_xml(self):
        with self.assertRaises(RuntimeError):
            sync.select_feed_products({})

    def test_main_purges_excluded_existing_and_respects_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / "njuskalo.xml"
            xml.write_text(
                "<ad_list>"
                "<ad_item><original_id>PCP-BULK-1</original_id></ad_item>"
                "<ad_item><original_id>SOLD</original_id></ad_item>"
                "<ad_item><original_id>A</original_id></ad_item>"
                "</ad_list>", encoding="utf-8",
            )
            current = {
                "PCP-BULK-1": item("PCP-BULK-1", 100),
                "SOLD": item("SOLD", 30, stock=0),
                "A": item("A", 60),
                "B": item("B", 20),
                "C": item("C", 30),
            }
            with (patch.object(sync, "MAX_ADS", 2),
                  patch.object(sync, "OUT", xml),
                  patch.object(sync, "auth", return_value="test"),
                  patch.object(sync, "shopify_products", return_value=current),
                  patch.object(sync, "cro_desc", return_value="Hrvatski opis"),
                  patch.object(sync, "save_translation_cache")):
                sync.main()
            found = [ad.findtext("original_id")
                     for ad in ET.parse(xml).getroot().findall("ad_item")]
            self.assertEqual({"A", "C"}, set(found))
            self.assertEqual(2, len(found))


if __name__ == "__main__":
    unittest.main()
