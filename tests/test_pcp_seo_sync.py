import unittest
from pcp_seo_sync import brand_from_title, reference_tokens, plan, sku_mpn

def product(title, extra=(), variant_extra=(), category=None, seo_title=None, seo_desc=None):
    def entries(items):
        return [{"namespace": n, "key": k, "type": "single_line_text_field", "value": v}
                for n, k, v in items]
    return {
        "id": "gid://shopify/Product/123", "title": title, "status": "ACTIVE",
        "productType": "Used auto parts bulk", "category": category,
        "seo": {"title": seo_title, "description": seo_desc},
        "mf": {"nodes": entries(extra)},
        "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/222",
                                "sku": "PCP-BULK-TEST-3X",
                                "mf": {"nodes": entries(variant_extra)}}]}
    }

class TestPcpSeo(unittest.TestCase):
    def test_brand_recognition(self):
        self.assertEqual("BOSCH", brand_from_title("3x VOLVO S40 I ECU / 0281010441 / 8200126494 / BOSCH BULK STOCK"))
        self.assertEqual("MAGNETI MARELLI", brand_from_title("FIAT ECU / 51766999 / MAGNETI M"))
        self.assertEqual("SIEMENS VDO", brand_from_title("FORD ECU / 7GAA / SIEMENS VDO"))
        self.assertIsNone(brand_from_title("10x ECU CD132 REFERENCE MATCH BULK STOCK"))

    def test_references(self):
        self.assertEqual(["0281010441", "HOM8200065996", "8200126494"],
                         reference_tokens("3x VOLVO S40 I ECU / 0281010441 / HOM8200065996 / 8200126494 / BOSCH BULK STOCK"))
        self.assertEqual([], reference_tokens("10x ECU CD132 REFERENCE MATCH BULK STOCK"))
        self.assertIsNone(sku_mpn("CD132"))

    def test_bulk_metadata(self):
        fields, patch, info = plan(product("3x VOLVO S40 I ECU / 0281010441 / HOM8200065996 / 8200126494 / BOSCH BULK STOCK"))
        values = {(x["namespace"], x["key"]): x["value"] for x in fields}
        self.assertEqual("BOSCH", values["custom", "pcp_manufacturer"])
        self.assertEqual("0281010441", values["mm-google-shopping", "mpn"])
        self.assertEqual("used", values["mm-google-shopping", "condition"])
        self.assertEqual("gid://shopify/TaxonomyCategory/vp-1-4-14", patch["category"])
        self.assertTrue(patch["seo"]["description"])
        self.assertEqual(3, info["references"])

    def test_do_not_overwrite_existing(self):
        original = [
            ("custom", "pcp_manufacturer", "SIEMENS"),
            ("custom", "mpn", "ABC123456"),
            ("pcp", "reference_numbers", '["ABC123456"]'),
            ("custom", "condition", "used")
        ]
        v = [("mm-google-shopping", "mpn", "ABC123456"),
             ("mm-google-shopping", "condition", "used")]
        x = product("RENAULT ECU / ABC123456 / BOSCH", original, v,
                    category={"id": "gid://shopify/TaxonomyCategory/vp-1-4-14"},
                    seo_title="SEO preserved", seo_desc="Existing description")
        fields, patch, _ = plan(x)
        self.assertIsNone(patch)
        self.assertFalse(any(f["key"] in ("pcp_manufacturer", "mpn") for f in fields))
        self.assertFalse(any(f["namespace"] == "mm-google-shopping" for f in fields))

    def test_only_corrupt_channel_brand_is_repaired(self):
        known = [("custom", "pcp_manufacturer", "BOSCH"),
                 ("shopify--channel-mapping", "channel_vendor_a7b241021b35",
                  "Primo Car Partscustom.pcp_manufacturer")]
        x = product("3x VOLVO ECU / 0281010441 / BOSCH BULK STOCK", known)
        fields, _, _ = plan(x)
        corrected = [f for f in fields if f["namespace"] == "shopify--channel-mapping"]
        self.assertEqual(["BOSCH"], [f["value"] for f in corrected])

        x = product("10x ECU CD132 REFERENCE MATCH BULK STOCK",
                    [("shopify--channel-mapping", "channel_vendor_a7b241021b35",
                      "Primo Car Partscustom.pcp_manufacturer")])
        fields, _, _ = plan(x)
        self.assertFalse(any(f["namespace"] == "shopify--channel-mapping" for f in fields))

    def test_generic_reference_group_without_mpn(self):
        fields, patch, _ = plan(product("10x ECU CD132 REFERENCE MATCH BULK STOCK"))
        self.assertFalse(any(f["key"] == "mpn" for f in fields))
        self.assertIsNone(next((x for x in fields if x["key"] == "pcp_manufacturer"), None))
        self.assertTrue(patch["category"])

if __name__ == "__main__":
    unittest.main()
