"""Primo Car Parts – add one non-destructive B2B cover to each selected product.

This test is intentionally restricted to 20 fixed product IDs. It neither
modifies nor removes any existing media, and never changes stock, prices, SEO,
variants, titles or product status. Idempotent: detects an existing cover.
"""
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

SHOP = os.getenv("SHOPIFY_SHOP", "72b63e-9f.myshopify.com")
API = "2026-07"
OVERLAY_URL = "https://cdn.shopify.com/s/files/1/0832/6266/9127/files/pcp-b2b-bulk-overlay-transparent.png?v=1793614245"
TEST_IDS = [
    "11238625345863", # B01-02
    "11238625444167", # B01-03
    "11238625542471", # B01-05
    "11238625739079", # B01-09
    "11238625902919", # B01-11
    "11238625935687", # B01-12
    "11238626001223", # B01-13
    "11238626033991", # B01-14
    "11238626197831", # B01-15
    "11238626296135", # B01-16
    "11238626525511", # B01-18
    "11238626689351", # B01-19
    "11238626853191", # B01-21
    "11238626951495", # B01-22
    "11238627115335", # B01-24
    "11238627213639", # B02-01
    "11238627279175", # B02-02
    "11238627475783", # B02-03
    "11238627868999", # B02-04
    "11238628000071", # B02-05
]
MARKER = "PCP B2B cover V1 | "
GET = """query($id:ID!){product(id:$id){
 id title status totalInventory variants(first:1){nodes{sku}}
 media(first:12){nodes{id alt mediaContentType ... on MediaImage{image{url width height}}}}
}}"""
STAGE = """mutation($input:[StagedUploadInput!]!){stagedUploadsCreate(input:$input){
 stagedTargets{url resourceUrl parameters{name value}} userErrors{field message}
}}"""
ADD = """mutation($p:ProductUpdateInput!,$m:[CreateMediaInput!]){productUpdate(product:$p,media:$m){
 product{id} userErrors{field message}
}}"""
MOVE = """mutation($id:ID!,$moves:[MoveInput!]!){productReorderMedia(id:$id,moves:$moves){
 job{id done} userErrors{field message}
}}"""


def shopify_token():
    client = os.environ["SHOPIFY_CLIENT_ID"]
    secret = os.environ["SHOPIFY_CLIENT_SECRET"]
    r = requests.post(
        f"https://{SHOP}/admin/oauth/access_token",
        data={"grant_type": "client_credentials",
              "client_id": client, "client_secret": secret}, timeout=45)
    r.raise_for_status()
    return r.json()["access_token"]


def gql(token, query, variables):
    r = requests.post(
        f"https://{SHOP}/admin/api/{API}/graphql.json",
        headers={"X-Shopify-Access-Token": token},
        json={"query": query, "variables": variables}, timeout=90)
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError("GraphQL errors: " + json.dumps(body["errors"])[:750])
    return body["data"]


def check_errors(payload):
    if payload.get("userErrors"):
        raise RuntimeError("Shopify userErrors: " + json.dumps(payload["userErrors"])[:850])


def fetch_product(token, numeric_id):
    return gql(token, GET, {"id": f"gid://shopify/Product/{numeric_id}"})["product"]


def get_image_media(product):
    return [m for m in product["media"]["nodes"]
            if m.get("mediaContentType") == "IMAGE" and m.get("image")]


def cover_for(product):
    return next((m for m in get_image_media(product)
                 if (m.get("alt") or "").startswith(MARKER)), None)


def composite_cover(original_bytes, overlay):
    original = Image.open(io.BytesIO(original_bytes))
    # We NEVER crop, resize or shift the original ECU image.
    bg = original.convert("RGBA")
    layer = overlay.resize(bg.size, Image.Resampling.LANCZOS)
    if layer.getpixel((bg.width // 2, bg.height // 2))[3] > 15:
        raise RuntimeError("Overlay background is not transparent, refusing to hide ECU")
    merged = Image.alpha_composite(bg, layer).convert("RGB")
    out = io.BytesIO()
    merged.save(out, format="PNG", optimize=True)
    return out.getvalue(), original.size


def upload_staged(token, file_bytes, filename):
    target_data = gql(token, STAGE, {"input": [{
        "filename": filename, "mimeType": "image/png",
        "resource": "IMAGE", "httpMethod": "POST"}]})["stagedUploadsCreate"]
    check_errors(target_data)
    targets = target_data["stagedTargets"]
    if len(targets) != 1:
        raise RuntimeError("Expected one staged upload target")
    target = targets[0]
    params = [(p["name"], p["value"]) for p in target["parameters"]]
    files = [("file", (filename, file_bytes, "image/png"))]
    r = requests.post(target["url"], data=params, files=files, timeout=120)
    r.raise_for_status()
    return target["resourceUrl"]


def wait_for_cover(token, numeric_id, timeout_s=100):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        p = fetch_product(token, numeric_id)
        result = cover_for(p)
        if result:
            return p, result
        time.sleep(4)
    raise RuntimeError("Cover uploaded but media is not yet visible; retry will not duplicate once ready")


def reorder_first(token, product_id, cover_id, numeric_id):
    payload = gql(token, MOVE, {"id": product_id,
                                  "moves": [{"id": cover_id, "newPosition": 0}]})["productReorderMedia"]
    check_errors(payload)
    for _ in range(18):
        p = fetch_product(token, numeric_id)
        media = get_image_media(p)
        if media and media[0]["id"] == cover_id:
            return p
        time.sleep(3)
    raise RuntimeError("Cover added but first-position reorder not confirmed")


def process(token, numeric_id, overlay, apply):
    p = fetch_product(token, numeric_id)
    if not p:
        return {"id": numeric_id, "status": "SKIP_NOT_FOUND"}
    sku = p["variants"]["nodes"][0]["sku"] if p["variants"]["nodes"] else numeric_id
    if p["status"] != "ACTIVE" or p["totalInventory"] < 1:
        return {"sku": sku, "status": "SKIP_INACTIVE_OR_SOLD"}
    if (re.match(r"^(3|5|10)x\\b", p["title"], re.I)
        or "BULK STOCK" in p["title"].upper()):
        return {"sku": sku, "status": "SKIP_BULK"}
    media = get_image_media(p)
    existing_cover = cover_for(p)
    if existing_cover:
        if media[0]["id"] != existing_cover["id"]:
            if apply:
                p = reorder_first(token, p["id"], existing_cover["id"], numeric_id)
        return {"sku": sku, "status": "ALREADY_EXISTS", "images": len(get_image_media(p))}
    # Only 3-image products are touched by this test, so 3 -> 4 exactly.
    if len(media) != 3 or len(p["media"]["nodes"]) != 3:
        return {"sku": sku, "status": "SKIP_NOT_3_IMAGES", "images": len(media)}
    original = media[0]["image"]
    if not original or not original.get("url"):
        return {"sku": sku, "status": "SKIP_MISSING_ORIGINAL"}
    response = requests.get(original["url"], timeout=60)
    response.raise_for_status()
    content, size = composite_cover(response.content, overlay)
    report = {"sku": sku, "id": numeric_id, "dimensions": list(size),
              "before": 3, "after": 4, "status": "DRY_RUN"}
    if not apply:
        return report
    filename = "PCP_B2B_COVER_V1_" + re.sub(r"[^A-Za-z0-9_-]", "-", sku) + ".png"
    source = upload_staged(token, content, filename)
    added = gql(token, ADD, {"p": {"id": p["id"]},
              "m": [{"mediaContentType": "IMAGE", "originalSource": source,
                      "alt": MARKER + p["title"]}]})["productUpdate"]
    check_errors(added)
    p, cover = wait_for_cover(token, numeric_id)
    p = reorder_first(token, p["id"], cover["id"], numeric_id)
    after = get_image_media(p)
    assert len(after) == 4, f"Expected four images, got {len(after)}"
    assert [m["id"] for m in after[1:]] == [m["id"] for m in media], (
        "Original image order changed; immediate review needed")
    report["status"] = "UPDATED"
    report["cover_id"] = cover["id"]
    return report


def main():
    # Applying is exclusively enabled by the dedicated 20-item test workflow.
    apply = os.getenv("APPLY_20", "0") == "1"
    limit = min(int(os.getenv("TEST_LIMIT", "20")), 20)
    token = shopify_token()
    r = requests.get(OVERLAY_URL, timeout=60)
    r.raise_for_status()
    overlay = Image.open(io.BytesIO(r.content)).convert("RGBA")
    if overlay.getpixel((overlay.width // 2, overlay.height // 2))[3] > 15:
        raise RuntimeError("The uploaded Shopify overlay has opaque background")
    results = []
    for numeric_id in TEST_IDS[:limit]:
        try:
            result = process(token, numeric_id, overlay, apply)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            result = {"id": numeric_id, "status": "ERROR", "reason": str(exc)[:500]}
            print(json.dumps(result, ensure_ascii=False), flush=True)
        results.append(result)
        time.sleep(0.5)
    Path("pcp_b2b_overlay_test_report.json").write_text(
        json.dumps({"apply": apply, "results": results}, indent=2, ensure_ascii=False))
    failures = sum(x["status"] == "ERROR" for x in results)
    print(json.dumps({"tested": len(results), "updated": sum(x["status"] == "UPDATED" for x in results),
                      "failed": failures, "apply": apply}))
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
