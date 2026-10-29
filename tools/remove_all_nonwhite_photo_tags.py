#!/usr/bin/env python3
"""Remove the single product tag PCP-PHOTO-1-NOT-WHITE everywhere in Shopify.
No changes to media, SKUs, prices, inventory, statuses, or other tags.
Uses product IDs from a live Shopify product-tag search, then verifies count 0.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

SHOP = "72b63e-9f.myshopify.com"
VERSION = "2026-07"
TAG = "PCP-PHOTO-1-NOT-WHITE"
REPORT = Path("shopify_remove_all_photo_tag_result.json")

def get_token():
    encoded = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["SHOPIFY_CLIENT_ID"],
        "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"],
    }).encode()
    req = urllib.request.Request(
        f"https://{SHOP}/admin/oauth/access_token",
        data=encoded, method="POST")
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)["access_token"]

def gql(token, query, variables=None):
    encoded = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(8):
        req = urllib.request.Request(
            f"https://{SHOP}/admin/api/{VERSION}/graphql.json",
            data=encoded,
            headers={"Content-Type": "application/json",
                     "X-Shopify-Access-Token": token},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                parsed = json.load(response)
            if parsed.get("errors"):
                raise RuntimeError("GraphQL error " + str(parsed["errors"])[:900])
            return parsed["data"]
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 7:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("Retries exhausted")

PRODUCTS = """query($after:String){
  products(first:100,after:$after,query:"tag:PCP-PHOTO-1-NOT-WHITE"){
    nodes{id tags}
    pageInfo{hasNextPage endCursor}
  }
}"""

def tagged(token):
    ids = {}
    cursor = None
    for _ in range(120):
        data = gql(token, PRODUCTS, {"after": cursor})["products"]
        for item in data["nodes"]:
            if TAG not in item["tags"]:
                raise RuntimeError(f"Product search returned untagged product {item['id']}")
            ids[item["id"]] = True
        if not data["pageInfo"]["hasNextPage"]:
            return set(ids)
        cursor = data["pageInfo"]["endCursor"]
    raise RuntimeError("Page limit exceeded while listing tagged products")

def save(report):
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    token = get_token()
    original = tagged(token)
    print("TARGETED_TAG", TAG, "INITIAL_PRODUCT_COUNT", len(original), flush=True)
    if len(original) > 10000:
        raise RuntimeError(f"Unexpectedly large tagged set: {len(original)}")
    result = {
        "tag": TAG,
        "initial_product_count": len(original),
        "successful_removals": [],
        "failed_removals": [],
        "remaining_ids": [],
    }
    save(result)

    # Repeatedly remove this precise tag from product IDs, without altering others.
    for first in range(0, len(original), 15):
        ids = sorted(original)[first:first + 15]
        mutations = [
            f'p{i}:tagsRemove(id:{json.dumps(pid)},tags:[{json.dumps(TAG)}])'
            + '{userErrors{field message}}'
            for i, pid in enumerate(ids)
        ]
        data = gql(token, "mutation CleanupTag {" + " ".join(mutations) + "}")
        for i, pid in enumerate(ids):
            errors = data[f"p{i}"]["userErrors"]
            if errors:
                result["failed_removals"].append({"id": pid, "errors": errors})
            else:
                result["successful_removals"].append(pid)
        save(result)
        if first % 75 == 0 or first + 15 >= len(original):
            print("REMOVAL_PROGRESS",len(result["successful_removals"]),"/",len(original),
                  "FAILURES",len(result["failed_removals"]),flush=True)
        time.sleep(0.45)

    if result["failed_removals"]:
        raise RuntimeError("Some Shopify tag removals failed; see report")
    # Shopify search index updates asynchronously. Do not assume missing update
    # until repeated reads confirm exact zero.
    for attempt in range(20):
        remaining = tagged(token)
        result["remaining_ids"] = sorted(remaining)
        result["remaining_tagged_count"] = len(remaining)
        result["verification_attempt"] = attempt + 1
        save(result)
        print("VERIFICATION",attempt+1,"REMAINING",len(remaining),flush=True)
        if not remaining:
            result["verification"] = "SUCCESS_ZERO_TAGGED_PRODUCTS"
            save(result)
            print("SUCCESS_TAG_REMOVED_FROM_ALL",len(original),"PRODUCTS",flush=True)
            return
        time.sleep(5)
    raise RuntimeError("Could not verify zero remaining products within index lag window")

if __name__ == "__main__":
    main()
