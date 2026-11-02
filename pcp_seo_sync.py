"""Primo Car Parts: conservative Shopify SEO maintenance (safe for daily runs).

Fills ONLY missing structured fields. Never changes prices, stock, handles, product
titles, B2B discounts, or vehicle fitment. Existing explicit values always win.
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request

SHOP = os.getenv("SHOPIFY_SHOP", "72b63e-9f.myshopify.com")
API = "2026-07"
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"
MAX_UPDATES = int(os.getenv("MAX_UPDATES", "350"))
ECU_CATEGORY = "gid://shopify/TaxonomyCategory/vp-1-4-14"
BRANDS = [
    ("MAGNETI MARELLI", r"\b(?:MAGNETI(?:[\s-]+M(?:ARELLI?)?)?|MARELLI)\b"),
    ("SIEMENS VDO", r"\bSIEMENS[\s-]+VDO\b"),
    ("AC DELCO", r"\bAC[\s-]?DELCO\b"),
    ("BOSCH", r"\bBOSCH\b"), ("CONTINENTAL", r"\bCONTINENTAL\b"),
    ("SIEMENS", r"\bSIEMENS\b"), ("SAGEM", r"\bSAGEM\b"),
    ("DENSO", r"\b(?:NIPPON[\s-]?)?DENSO\b"), ("HITACHI", r"\bHITACHI\b"),
    ("LUCAS", r"\bLUCAS\b"), ("VALEO", r"\bVALEO\b"), ("DELPHI", r"\bDELPHI\b"),
    ("VISTEON", r"\bVISTEON\b"), ("VDO", r"\bVDO\b"),
    ("TRW", r"\bTRW\b"), ("HELLA", r"\bHELLA\b"), ("WEBER", r"\bWEBER\b"),
    ("KEIHIN", r"\bKEIHIN\b"), ("KEMSCO", r"\bKEMSCO\b"),
    ("KEFICO", r"\bKEFICO\b"), ("METATRON", r"\bMETATRON\b"),
    ("KOYO", r"\bKOYO\b"), ("DELCO", r"\bDELCO\b"),
    ("AUTOLIV", r"\bAUTOLIV\b"), ("TEMIC", r"\bTEMIC\b"),
    ("MOTOROLA", r"\bMOTOROLA\b"), ("BENDIX", r"\bBENDIX\b"),
]
QUERY = """query($after:String) {
 products(first:100, after:$after, sortKey:ID, reverse:true) {
  nodes {id title status productType category{id} seo{title description}
    mf:metafields(first:45){nodes{namespace key type value}}
    variants(first:1){nodes{id sku mf:metafields(first:20){nodes{namespace key type value}}}}
  }
  pageInfo{endCursor hasNextPage}
 }
}"""
SET_META = """mutation($fields:[MetafieldsSetInput!]!){
 metafieldsSet(metafields:$fields){metafields{id} userErrors{field message code}}
}"""
UPDATE_PRODUCT = """mutation($p:ProductUpdateInput!){
 productUpdate(product:$p){product{id}userErrors{field message}}
}"""


def brand_from_title(title):
    # Most PCParts ECU listings put manufacturer at end, immediately before BULK STOCK.
    text = re.sub(r"\bBULK[\s-]+STOCK\b", "", title.upper()).strip()
    matches = []
    for name, pattern in BRANDS:
        hit = re.search(pattern, text)
        if hit and hit.start() >= max(0, len(text) - 55):
            matches.append(name)
    if "SIEMENS VDO" in matches:
        matches = [x for x in matches if x not in ("SIEMENS", "VDO")]
    if "AC DELCO" in matches:
        matches = [x for x in matches if x != "DELCO"]
    return matches[0] if len(matches) == 1 else None


def reference_tokens(title):
    # Never use vehicle name, engine, a bulk group ID, or internal SKU as a made-up MPN.
    refs = []
    for part in title.upper().split("/")[1:]:
        value = re.sub(r"\bBULK[\s-]+STOCK\b", "", part).strip(" -")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.+ _-]{5,50}", value):
            continue
        if any(x in value for x in ("REFERENCE MATCH", " ECU ", " STK ", "BULK", " OEM ", "VIN")):
            continue
        clean = re.sub(r"[\s._-]", "", value)
        if len(clean) < 7 or not re.search(r"\d", clean):
            continue
        if value not in refs:
            refs.append(value)
    return refs[:10]


def sku_mpn(value):
    cleaned = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
    return cleaned if len(cleaned) >= 7 and re.search(r"\d", cleaned) else None


def seo_description(title, ecu):
    suffix = (" Used original ECU; compare OEM/HW/SW references and VIN before purchase."
              " Coding or immobilizer adaptation may be required." if ecu else
              " Used original car part; verify OE references, fitment and photos before purchase.")
    return (title + "." + suffix)[:158].rstrip(" .,;") + "."


def indexed(nodes):
    return {(x["namespace"], x["key"]): x for x in nodes}


def add_missing(fields, owner, current, namespace, key, value, kind="single_line_text_field"):
    if value is None or (namespace, key) in current and current[namespace, key]["value"].strip():
        return
    fields.append({"ownerId": owner, "namespace": namespace, "key": key,
                   "type": kind, "value": str(value)})


def plan(product):
    title = product["title"]
    is_ecu = bool(re.search(r"\b(?:ECU|ECM|ENGINE CONTROL UNIT)\b", title, re.I))
    is_bulk = bool(re.search(r"\bBULK[\s-]+STOCK\b|^\s*(?:3|5|10)x\b", title, re.I))
    metas = indexed(product["mf"]["nodes"])
    variant = product["variants"]["nodes"][0] if product["variants"]["nodes"] else None
    vmeta = indexed(variant["mf"]["nodes"]) if variant else {}
    brand = (metas.get(("custom", "pcp_manufacturer"), {}).get("value")
             or metas.get(("pcp", "manufacturer"), {}).get("value") or "")
    if brand.strip().upper() in ("UNSPECIFIED", "UNKNOWN", "PRIMO CAR PARTS"):
        brand = ""
    brand = brand or brand_from_title(title)
    refs_value = metas.get(("pcp", "reference_numbers"), {}).get("value")
    refs = []
    if refs_value:
        try:
            refs = json.loads(refs_value)
        except (ValueError, TypeError):
            refs = []
    if not refs:
        refs = [x.strip() for x in metas.get(("custom", "pcp_oem_references"), {}).get("value", "").splitlines() if x.strip()]
    if not refs:
        refs = reference_tokens(title)
    current_mpn = metas.get(("custom", "mpn"), {}).get("value")
    mpn = sku_mpn(current_mpn) or next((sku_mpn(x) for x in refs if sku_mpn(x)), None)
    fields = []
    add_missing(fields, product["id"], metas, "custom", "pcp_manufacturer", brand)
    # Correct the known corrupt Google channel override only if the actual brand
    # is established. Do not change legitimate overrides or unknown manufacturers.
    channel = metas.get(("shopify--channel-mapping", "channel_vendor_a7b241021b35"))
    if (brand and channel and
            channel["value"].strip().lower() == "primo car partscustom.pcp_manufacturer"):
        fields.append({"ownerId": product["id"],
                       "namespace": "shopify--channel-mapping",
                       "key": "channel_vendor_a7b241021b35",
                       "type": channel.get("type") or "single_line_text_field",
                       "value": brand})
    add_missing(fields, product["id"], metas, "custom", "condition", "used")
    if refs:
        add_missing(fields, product["id"], metas, "pcp", "reference_numbers",
                    json.dumps(refs, ensure_ascii=False), "list.single_line_text_field")
        add_missing(fields, product["id"], metas, "custom", "pcp_oem_references",
                    "\n".join(refs), "multi_line_text_field")
    if mpn:
        add_missing(fields, product["id"], metas, "custom", "mpn", mpn)
        if variant:
            add_missing(fields, variant["id"], vmeta, "mm-google-shopping", "mpn", mpn)
    if variant:
        add_missing(fields, variant["id"], vmeta, "mm-google-shopping", "condition", "used")
    product_patch = {"id": product["id"]}
    if not product["seo"]["title"] or not product["seo"]["description"]:
        seo = {}
        if not product["seo"]["title"]:
            seo["title"] = title[:80]
        if not product["seo"]["description"]:
            seo["description"] = seo_description(title, is_ecu)
        product_patch["seo"] = seo
    if is_bulk and is_ecu and not product["category"]:
        product_patch["category"] = ECU_CATEGORY
    if len(product_patch) == 1:
        product_patch = None
    return fields, product_patch, {"bulk": is_bulk, "ecu": is_ecu, "mpn": mpn,
                                   "manufacturer": brand, "references": len(refs)}


def authorize():
    client = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
    secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
    if not client or not secret:
        raise RuntimeError("Missing Shopify client credentials in GitHub Actions secrets")
    body = urllib.parse.urlencode({"grant_type": "client_credentials",
                                   "client_id": client, "client_secret": secret}).encode()
    req = urllib.request.Request("https://" + SHOP + "/admin/oauth/access_token",
                                 data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)["access_token"]


def gql(token, query, variables):
    req = urllib.request.Request(
        "https://" + SHOP + "/admin/api/" + API + "/graphql.json",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token})
    with urllib.request.urlopen(req, timeout=90) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError("Shopify GraphQL error: " + str(result["errors"])[:400])
    return result["data"]


def write_fields(token, fields):
    for start in range(0, len(fields), 25):
        result = gql(token, SET_META, {"fields": fields[start:start + 25]})["metafieldsSet"]
        if result["userErrors"]:
            raise RuntimeError("Shopify metafield write failed: " + str(result["userErrors"])[:500])
        time.sleep(0.35)


def main():
    token = authorize()
    cursor = None
    processed = changed = missing_image_attention = 0
    while True:
        data = gql(token, QUERY, {"after": cursor})["products"]
        for product in data["nodes"]:
            if product["status"] != "ACTIVE":
                continue
            processed += 1
            fields, patch, info = plan(product)
            if not fields and not patch:
                continue
            changed += 1
            print(json.dumps({"product": product["id"], "sku": product["variants"]["nodes"][0]["sku"]
                              if product["variants"]["nodes"] else None, "dry_run": DRY_RUN,
                              "fields": [(f["namespace"], f["key"]) for f in fields],
                              "seo_or_category": list(patch or {}), "bulk": info["bulk"]}))
            if not DRY_RUN:
                if fields:
                    write_fields(token, fields)
                if patch:
                    result = gql(token, UPDATE_PRODUCT, {"p": patch})["productUpdate"]
                    if result["userErrors"]:
                        raise RuntimeError("Product SEO update failed: " + str(result["userErrors"])[:500])
            if changed >= MAX_UPDATES:
                print("STOP: per-run safety cap", MAX_UPDATES)
                return
        if not data["pageInfo"]["hasNextPage"]:
            break
        cursor = data["pageInfo"]["endCursor"]
    print(json.dumps({"products_checked": processed, "products_updated_or_planned": changed,
                      "dry_run": DRY_RUN}))


if __name__ == "__main__":
    main()
