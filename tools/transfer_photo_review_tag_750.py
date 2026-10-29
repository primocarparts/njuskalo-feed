#!/usr/bin/env python3
"""Shift PCP-PHOTO-1-NOT-WHITE from the prior 72 products to the 750
first-image PREVERI products in the 2026-10-28 catalog photo audit.
Only product tags are mutated; all other product data is untouched.
Address products by Shopify product GID, NOT SKU (SKU B53-15 appears twice).
"""
import csv
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

SHOP = "72b63e-9f.myshopify.com"
API = "2026-07"
TAG = "PCP-PHOTO-1-NOT-WHITE"
AUDIT = Path("audit_results/VSE_PREVERJENE_FOTOGRAFIJE.csv")
OUT = Path("photo_review_750_tag_transfer_result.json")

def auth():
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["SHOPIFY_CLIENT_ID"],
        "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"],
    }).encode()
    request = urllib.request.Request(
        f"https://{SHOP}/admin/oauth/access_token",
        data=form, method="POST")
    with urllib.request.urlopen(request, timeout=45) as rsp:
        return json.load(rsp)["access_token"]

def graphql(tok, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    for attempt in range(8):
        request = urllib.request.Request(
            f"https://{SHOP}/admin/api/{API}/graphql.json",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": tok,
            },
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=90) as rsp:
                payload = json.load(rsp)
            if payload.get("errors"):
                raise RuntimeError("Shopify GraphQL errors: " + str(payload["errors"])[:1100])
            return payload["data"]
        except urllib.error.HTTPError as err:
            if err.code not in (429, 500, 502, 503, 504) or attempt == 7:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("Shopify rate-limit retry exhaustion")

def chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]

TAGGED_QUERY = """query TaggedPhotos($after:String) {
  products(first:100,after:$after,query:"tag:PCP-PHOTO-1-NOT-WHITE") {
    nodes{id tags variants(first:20){nodes{sku}}}
    pageInfo{endCursor hasNextPage}
  }
}"""

def get_tagged(tok):
    pages = []; cursor = None
    for _ in range(70):
        result = graphql(tok, TAGGED_QUERY, {"after": cursor})["products"]
        pages.extend(result["nodes"])
        if not result["pageInfo"]["hasNextPage"]:
            return pages
        cursor = result["pageInfo"]["endCursor"]
    raise RuntimeError("Pagination safety limit reached")

NODE_QUERY = """query AuditProducts($ids:[ID!]!) {
  nodes(ids:$ids) {
    ... on Product {
      id status tags
      variants(first:20){nodes{sku}}
    }
  }
}"""

def run_mutation(tok, name, ids):
    successes = []; failures = []
    for pack in chunks(ids, 10):
        members = []
        for j, pid in enumerate(pack):
            members.append(
                f'x{j}:{name}(id:{json.dumps(pid)},tags:[{json.dumps(TAG)}])'
                + '{userErrors{field message}}'
            )
        response = graphql(tok, "mutation MovePhotoTags { " + " ".join(members) + " }")
        for j, pid in enumerate(pack):
            errors = response[f"x{j}"]["userErrors"]
            if errors:
                failures.append({"product_id": pid, "errors": errors})
            else:
                successes.append(pid)
        if (len(successes) + len(failures)) % 100 == 0:
            print(name, "completed", len(successes), "failures", len(failures), flush=True)
        time.sleep(0.5)
    return successes, failures

def verify(tok, expected_ids):
    for i in range(12):
        result = get_tagged(tok)
        actual_ids = {row["id"] for row in result}
        if actual_ids == expected_ids:
            return result
        print("Index catch-up check", i + 1,
              "tagged", len(actual_ids),
              "unexpected", len(actual_ids - expected_ids),
              "missing", len(expected_ids - actual_ids), flush=True)
        time.sleep(6)
    raise RuntimeError(
        f"Final Shopify tag verification failed: expected {len(expected_ids)}, "
        f"got {len(actual_ids)}, missing {len(expected_ids-actual_ids)}, "
        f"unexpected {len(actual_ids-expected_ids)}")

def main():
    with AUDIT.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    review = [r for r in rows if r["slika_1_ozadje"] == "PREVERI"]
    older = [r for r in rows if r["slika_1_ozadje"] == "NI_BELO"]
    assert len(review) == 750 and len(older) == 322, "Audit row counts changed"
    assert all(r["status"] == "ACTIVE" for r in review), "Unexpected review status in audit"

    expected_by_id = {}
    for row in review:
        pid = "gid://shopify/Product/" + row["shopify_link"].rstrip("/").split("/")[-1]
        if pid in expected_by_id:
            raise RuntimeError("Duplicate Product ID in original audit " + pid)
        expected_by_id[pid] = row["sku"]
    expected_ids = set(expected_by_id)
    older_ids = {
        "gid://shopify/Product/" + r["shopify_link"].rstrip("/").split("/")[-1]
        for r in older
    }
    assert len(expected_ids) == 750 and len(older_ids) == 322
    assert not (expected_ids & older_ids), "Old and new target groups overlap"

    tok = auth()
    already = get_tagged(tok)
    old_ids = [p["id"] for p in already]
    oldset = set(old_ids)
    if len(old_ids) != 72 or len(oldset) != 72:
        raise RuntimeError(f"Expected EXACT 72 already-tagged products, got {len(old_ids)}")
    if not oldset <= older_ids:
        raise RuntimeError("Current tagged products contain a product outside previous 322 non-white set")
    if oldset & expected_ids:
        raise RuntimeError("Unexpected overlap between 72 old tags and 750 review products")

    print("Verified 72 old tags, 750 distinct review Product IDs, no group overlap", flush=True)
    # Validate all 750 historical IDs, but don't fail because Shopify sold/retired a product.
    # Never attach a tag to a replacement with an unverified SKU or Product ID.
    confirmed = []
    missing = []
    changed = []
    pretagged = []
    for block in chunks(sorted(expected_ids), 40):
        found = graphql(tok, NODE_QUERY, {"ids": block})["nodes"]
        if len(found) != len(block):
            raise RuntimeError("Incorrect product nodes length")
        for requested, p in zip(block, found):
            sku = expected_by_id[requested]
            if not p or p.get("id") != requested:
                missing.append({"product_id": requested, "sku": sku, "reason": "DELETED_OR_UNAVAILABLE"})
                continue
            if sku not in [n.get("sku") for n in p["variants"]["nodes"]]:
                changed.append({"product_id": requested, "sku": sku, "reason": "SKU_CHANGED"})
                continue
            if TAG in p["tags"]:
                pretagged.append(requested)
            confirmed.append(requested)
    if len(confirmed) < 650:
        raise RuntimeError(f"Too many missing/changed historical products: {len(confirmed)} valid from 750")
    print("Shopify preflight: original=750, existing verified=", len(confirmed),
          "missing=",len(missing),"changed=",len(changed), "pretagged=",len(pretagged),flush=True)

    removed, removal_errors = run_mutation(tok, "tagsRemove", sorted(oldset))
    if removal_errors or len(removed) != 72:
        raise RuntimeError("Failed old tag removal: " + str(removal_errors[:5]))
    print("Removed old tag from 72/72 previous products; beginning transfer", flush=True)

    added, add_errors = run_mutation(tok, "tagsAdd", sorted(set(confirmed) - set(pretagged)))
    report = {
        "tag": TAG,
        "original_tagged_products": len(oldset),
        "original_tags_removed": len(removed),
        "original_review_rows": 750,
        "valid_existing_review_products": len(confirmed),
        "deleted_or_unavailable": missing,
        "changed_skus_skipped": changed,
        "new_review_tag_mutations": len(added),
        "already_tagged_review_products": len(pretagged),
        "add_mutation_failures": add_errors,
        "duplicate_sku_note": "B53-15 appeared on two distinct Product IDs, both included when present",
        "updated_product_ids": added,
        "old_group_product_ids": old_ids,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if add_errors or len(added)+len(pretagged) != len(confirmed):
        raise RuntimeError("Not all valid review tags added; see report")
    verified = verify(tok, set(confirmed))
    report["final_verified_tagged_products"] = len(verified)
    report["verification"] = "EXACT_MATCH_EXISTING_AUDITED_PRODUCTS"
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SUCCESS: TAG TRANSFER COMPLETE: old=0, new=",len(confirmed),
          "deleted=",len(missing),"changed=",len(changed),flush=True)

if __name__ == "__main__":
    main()
