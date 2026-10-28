#!/usr/bin/env python3
"""Tag exactly the 322 individually verified first-photo nonwhite products (Shopify read-verified)."""
import csv, json, os, time, urllib.request, urllib.parse, urllib.error
from pathlib import Path

SHOP="72b63e-9f.myshopify.com"
API="2026-07"
TAG="PCP-PHOTO-1-NOT-WHITE"
CSV=Path("audit_results/VSE_PREVERJENE_FOTOGRAFIJE.csv")
RUNLOG=Path("tag_first_photo_result.json")

def client_token():
    payload=urllib.parse.urlencode({"grant_type":"client_credentials",
       "client_id":os.environ["SHOPIFY_CLIENT_ID"],
       "client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]}).encode()
    req=urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token",data=payload,method="POST")
    with urllib.request.urlopen(req,timeout=35) as f:
        return json.load(f)["access_token"]

def gql(token,query,vars=None):
    reqdata=json.dumps({"query":query,"variables":vars or {}}).encode()
    for attempt in range(6):
        req=urllib.request.Request(f"https://{SHOP}/admin/api/{API}/graphql.json",
            data=reqdata,headers={"Content-Type":"application/json",
              "X-Shopify-Access-Token":token},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=90) as resp:
                obj=json.load(resp)
            if "errors" in obj:
                raise RuntimeError(str(obj["errors"])[:1000])
            return obj["data"]
        except urllib.error.HTTPError as err:
            if err.code not in (429,500,502,503,504) or attempt==5:
                raise
            time.sleep(2*(attempt+1))
    raise RuntimeError("GraphQL request failed")

def batches(items,n):
    for i in range(0,len(items),n):
        yield items[i:i+n]

def main():
    with CSV.open(encoding="utf-8-sig",newline="") as fp:
        data=list(csv.DictReader(fp,delimiter=";"))
    target=[r for r in data if r["slika_1_ozadje"]=="NI_BELO"]
    ids=[f"gid://shopify/Product/{r['product_id']}" for r in target]
    assert len(target)==322 and len(set(ids))==322, "EXPECTED EXACTLY 322 UNIQUE PRODUCTS"
    assert all(r["status"]=="ACTIVE" for r in target), "Status must be active from audit"
    exp=dict(zip(ids,[r["sku"] for r in target]))
    tok=client_token()
    query="""query Check($ids:[ID!]!){nodes(ids:$ids){... on Product{id tags variants(first:100){nodes{sku}}}}}"""
    safe=[]; old_tagged=[]
    for batch in batches(ids,50):
        found=gql(tok,query,{"ids":batch})["nodes"]
        for id,item in zip(batch,found):
            if not item or item.get("id")!=id:
                raise RuntimeError("Product ID missing: "+id)
            if exp[id] not in [n.get("sku") for n in item["variants"]["nodes"]]:
                raise RuntimeError("SKU has changed on product "+id+" (expected "+exp[id]+")")
            if TAG in item["tags"]: old_tagged.append(id)
            safe.append(id)
    print("SKU and product-ID verification passed:",len(safe),"already tagged",len(old_tagged),flush=True)
    added=[];failures=[]
    for index,pack in enumerate(batches(safe,12),1):
        aliases=[]
        for j,id in enumerate(pack):
            aliases.append(f'p{j}:tagsAdd(id:"{id}",tags:["{TAG}"])'+'{userErrors{field message}}')
        response=gql(tok,"mutation TagNonWhite { "+" ".join(aliases)+" }")
        for j,id in enumerate(pack):
            errs=response[f"p{j}"]["userErrors"]
            if errs: failures.append({"product_id":id,"sku":exp[id],"errors":errs})
            else: added.append(id)
        if index%5==0 or index==1: print("Tag requests done:",len(added),"/",len(safe),"failures",len(failures),flush=True)
        time.sleep(0.55)
    check="""query Verify($after:String,$query:String!){products(first:250,after:$after,query:$query){nodes{id}pageInfo{hasNextPage endCursor}}}"""
    foundset=set()
    for poll in range(5):
        foundset.clear()
        after=None
        while True:
            p=gql(tok,check,{"after":after,"query":f"tag:{TAG}"})["products"]
            foundset.update(v["id"] for v in p["nodes"])
            if not p["pageInfo"]["hasNextPage"]: break
            after=p["pageInfo"]["endCursor"]
        if foundset==set(ids):break
        time.sleep(4)
    summary={"tag":TAG,"expected":len(target),"successful_mutations":len(added),
         "already_had_tag":len(old_tagged),"verified_matching_ids":len(foundset&set(ids)),
         "unexpected_ids":sorted(foundset-set(ids)),"missing_ids":sorted(set(ids)-foundset),
         "failures":failures}
    RUNLOG.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    print("TAGGING_RESULT",json.dumps({k:v if not isinstance(v,list) else len(v) for k,v in summary.items()},ensure_ascii=False),flush=True)
    if failures or foundset!=set(ids):raise RuntimeError("Tagging incomplete; see tag_first_photo_result.json")
    print("VERIFIED EXACTLY 322 MARKED SHOPIFY PRODUCTS",flush=True)
if __name__=="__main__":main()
