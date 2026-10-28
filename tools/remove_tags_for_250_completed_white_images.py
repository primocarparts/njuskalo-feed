#!/usr/bin/env python3
"""Remove only PCP-PHOTO-1-NOT-WHITE from 250 verified UPDATED products.
Do not touch the other 70 failed or 2 already-white products.
No changes to images, prices, inventory, or unrelated tags.
"""
import json,os,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path

SHOP="72b63e-9f.myshopify.com"
API="2026-07"
TAG="PCP-PHOTO-1-NOT-WHITE"
REPORT=Path("white_bg_tag_cleanup_report.json")
INPUT=Path("audit_results/white_bg_250_success_skus.json")

def token():
    q=urllib.parse.urlencode({"grant_type":"client_credentials",
        "client_id":os.environ["SHOPIFY_CLIENT_ID"],
        "client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]}).encode()
    req=urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token",data=q,method="POST")
    with urllib.request.urlopen(req,timeout=40) as r:return json.load(r)["access_token"]

def gql(tok, query, variables=None):
    for n in range(7):
        req=urllib.request.Request(
            f"https://{SHOP}/admin/api/{API}/graphql.json",
            data=json.dumps({"query":query,"variables":variables or {}}).encode(),
            headers={"Content-Type":"application/json","X-Shopify-Access-Token":tok},
            method="POST")
        try:
            with urllib.request.urlopen(req,timeout=90) as r: obj=json.load(r)
            if obj.get("errors"):raise RuntimeError("Shopify GraphQL error "+str(obj["errors"])[:800])
            return obj["data"]
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or n==6:raise
            time.sleep(n*2+2)
    raise RuntimeError("GraphQL retry exhausted")

def tagged(tok):
    q="""query($after:String){products(first:100,after:$after,query:"tag:PCP-PHOTO-1-NOT-WHITE"){
        nodes{id tags variants(first:10){nodes{sku}}}
        pageInfo{hasNextPage endCursor}}}"""
    result=[]; after=None
    while True:
        a=gql(tok,q,{"after":after})["products"]
        result+=a["nodes"]
        if not a["pageInfo"]["hasNextPage"]:break
        after=a["pageInfo"]["endCursor"]
    return result

def main():
    data=json.loads(INPUT.read_text(encoding="utf-8"))
    success=set(data["successful_skus"]); failed=set(data["failed_skus"]); already=set(data["already_white_skus"])
    assert data["source_run_id"]==40782000840 and data["tag"]==TAG
    assert len(success)==250 and len(failed)==70 and len(already)==2
    assert not (success&failed or success&already or failed&already)
    tok=token()
    before=tagged(tok)
    mapping={}
    for p in before:
        skus=[v.get("sku") for v in p["variants"]["nodes"]]
        matched=[s for s in skus if s in success or s in failed or s in already]
        if len(matched)!=1:raise RuntimeError("Unexpected tagged SKU or variants: "+str(skus))
        if TAG not in p["tags"]:raise RuntimeError("Missing target tag on filtered product")
        sku=matched[0]
        if sku in mapping:raise RuntimeError("Duplicate SKU in results "+sku)
        mapping[sku]=p["id"]
    if len(before)!=322 or set(mapping)!=(success|failed|already):
        raise RuntimeError(f"SAFETY ABORT: before tagged={len(before)}, expected=322, mismatches={sorted((success|failed|already)^set(mapping))[:15]}")
    print("Verified exact set of 322 tagged products; 250 success SKU candidates",flush=True)
    targets=sorted(success)
    completed=[];errors=[]
    for start in range(0,len(targets),10):
        batch=targets[start:start+10]
        mutation="mutation CleanupVerifiedFirstImages{ "+ " ".join(
            "x"+str(i)+":tagsRemove(id:"+json.dumps(mapping[sku])+",tags:["+json.dumps(TAG)+"]){userErrors{field message}}"
            for i,sku in enumerate(batch))+" }"
        data=gql(tok,mutation)
        for i,sku in enumerate(batch):
            errs=data["x"+str(i)]["userErrors"]
            if errs:errors.append({"sku":sku,"errors":errs})
            else:completed.append(sku)
        if (start//10)%5==0 or start+10>=len(targets):
            print("Tag removed:",len(completed),"/250, errors:",len(errors),flush=True)
        time.sleep(0.7)
    remaining=tagged(tok)
    remain_skus=set()
    for p in remaining:
        for v in p["variants"]["nodes"]:
            if v.get("sku") in failed|already:remain_skus.add(v.get("sku"))
    result={"tag":TAG,"source_run_id":40782000840,
            "requested_success_skus":len(success),"successfully_removed_mutations":len(completed),
            "mutation_errors":errors,"remaining_tagged_count":len(remaining),
            "expected_remaining_count":72,
            "remaining_expected_failed_or_already_white":len(remain_skus),
            "unexpected_remaining_ids":[p["id"] for p in remaining if not any(v.get("sku") in failed|already for v in p["variants"]["nodes"])],
            "removed_skus":completed}
    REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("CLEANUP_SUMMARY",json.dumps({k:v for k,v in result.items() if k!="removed_skus"},ensure_ascii=False),flush=True)
    if errors or len(completed)!=250 or len(remaining)!=72 or remain_skus!=(failed|already) or result["unexpected_remaining_ids"]:
        raise RuntimeError("Tag cleanup verification failed; inspect report")
    print("SUCCESS: Exactly 250 tags removed, 70 failed + 2 already-white still tagged",flush=True)

if __name__=="__main__":main()
