#!/usr/bin/env python3
"""Second-pass proof: compare OCR catalog numbers against OTHER Shopify product
titles, improve REVIEW -> MISMATCH when a matching reference belongs to an
incompatible vehicle brand. NEVER infer mismatch merely from failed OCR.
Only tag changes; no edits to catalog content or photos.
"""
import collections,csv,json,os,re,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path
ROOT=Path("photo_title_audit")
TAG_M="PCP-IMAGE-TITLE-MISMATCH"
TAG_R="PCP-IMAGE-TITLE-REVIEW"
SHOP="72b63e-9f.myshopify.com";API="2026-07"
GROUPS=[
 {"ALFA","FIAT","LANCIA","JEEP","CHRYSLER","DODGE","OPEL","PEUGEOT","CITROEN"},
 {"VW","AUDI","SEAT","SKODA","PORSCHE"},
 {"RENAULT","DACIA","NISSAN","MITSUBISHI"},
 {"HYUNDAI","KIA"},{"TOYOTA","LEXUS"},{"FORD","MAZDA"},
 {"OPEL","CHEVROLET","DAEWOO"}
]

def same_group(a,b):
 return a==b or any(a in g and b in g for g in GROUPS)

def get_token():
 payload=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":os.environ["SHOPIFY_CLIENT_ID"],
  "client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]}).encode()
 req=urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token",data=payload,method="POST")
 with urllib.request.urlopen(req,timeout=45) as r:return json.load(r)["access_token"]

def graphql(tok,q,variables=None):
 payload=json.dumps({"query":q,"variables":variables or {}}).encode()
 for a in range(8):
  req=urllib.request.Request(f"https://{SHOP}/admin/api/{API}/graphql.json",
    data=payload,headers={"Content-Type":"application/json","X-Shopify-Access-Token":tok},method="POST")
  try:
   with urllib.request.urlopen(req,timeout=85) as r:response=json.load(r)
   if response.get("errors"):raise RuntimeError(str(response["errors"])[:1000])
   return response["data"]
  except urllib.error.HTTPError as e:
   if e.code not in (429,500,502,503,504) or a==7:raise
   time.sleep(2*(a+1))
 raise RuntimeError("GraphQL retries exhausted")

def batch(items,n):
 for i in range(0,len(items),n):yield items[i:i+n]

def main():
 snapshot=json.loads((ROOT/"shopify_catalog_snapshot.json").read_text())
 files=sorted(ROOT.glob("results_shard_*.json"))
 if len(files)!=6:raise RuntimeError("Six shards missing, found "+str(len(files)))
 rows=[]
 for f in files:rows+=json.loads(f.read_text())
 if len(rows)!=len(snapshot) or len({r["product_id"] for r in rows})!=len(snapshot):
  raise RuntimeError("OCR result completeness failed")
 # Product titles in same store are a strong reference database for photo labels.
 idx=collections.defaultdict(list)
 for r in rows:
  for ref in r.get("title_refs",[]):
   if len(ref)>=8 and sum(c.isdigit() for c in ref)>=6:
    idx[ref].append(r)
 changed=[]
 for row in rows:
  if row["classification"]!="REVIEW" or row.get("matched_refs"):continue
  if not row.get("title_brands"):continue
  foreign_evidence=[]
  for img in row.get("ocr_images",[]):
   for number in img.get("reference_candidates",[]):
    if len(number)<8 or sum(c.isdigit() for c in number)<6:continue
    for foreign in idx.get(number,[]):
     if foreign["product_id"]==row["product_id"]:continue
     other_brands=foreign.get("title_brands",[])
     if not other_brands:continue
     if any(same_group(a,b) for a in row["title_brands"] for b in other_brands):continue
     foreign_evidence.append({
       "image_index":img["image_index"],
       "photo_catalog_number":number,
       "other_product_id":foreign["product_id"],
       "other_product_sku":foreign["sku"],
       "other_product_title":foreign["title"]
     })
  if foreign_evidence:
   # Favor exact printed catalog references from another unrelated vehicle.
   evidence=sorted(foreign_evidence,
       key=lambda d:(d["photo_catalog_number"],d["other_product_id"]))
   row["classification"]="MISMATCH"
   row["reason"]="Photo part number matches different-car Shopify title: "+evidence[0]["photo_catalog_number"]+" -> "+evidence[0]["other_product_title"]
   row["cross_catalog_evidence"]=evidence[:10]
   changed.append(row)

 print("CROSS_REF_UPGRADES",len(changed),
       "EXAMPLE",[(r["sku"],r["cross_catalog_evidence"][0]["photo_catalog_number"]) for r in changed[:12]],flush=True)
 final={"scanned_products":len(rows),"final_categories":dict(collections.Counter(r["classification"] for r in rows)),
        "cross_catalog_upgrades":len(changed),"upgraded_skus":[r["sku"] for r in changed],
        "details":[{"product_id":r["product_id"],"sku":r["sku"],"reason":r["reason"],
                "cross_catalog_evidence":r["cross_catalog_evidence"]} for r in changed]}
 (ROOT/"cross_catalog_final_report.json").write_text(json.dumps(final,ensure_ascii=False,indent=1))
 with (ROOT/"photo_title_final_results.csv").open("w",newline="",encoding="utf-8-sig") as f:
  w=csv.writer(f,delimiter=";")
  w.writerow(["SKU","Shopify Product ID","Title","Classification","Reason","Title Part Numbers","OCR Matched Numbers","Detected Brands","Shopify Link","Image 1","Image 2","Image 3"])
  for r in sorted(rows,key=lambda r:r["sku"]):
   images=r.get("image_urls",[])
   w.writerow([r["sku"],r["product_id"],r["title"],r["classification"],r["reason"],
       "|".join(r.get("title_refs",[])),"|".join(r.get("matched_refs",[])),
       "|".join(r.get("detected_brands",[])),
       "https://admin.shopify.com/store/72b63e-9f/products/"+r["product_id"].split("/")[-1],
       *[images[i] if i<len(images) else "" for i in range(3)]])
 if not changed:
  print("NO_UPGRADES_NEEDED",flush=True);return

 # Full base workflow has already completed, including its Shopify tagging.
 # Validate live title+SKU+source images before changing tags.
 tok=get_token()
 q="""query($ids:[ID!]!){nodes(ids:$ids){... on Product{
   id title tags variants(first:5){nodes{sku}}
   media(first:4){nodes{... on MediaImage{image{url}}}}
 }}}"""
 original={s["product_id"]:s for s in snapshot}
 verified=[]
 skipped=[]
 for pack in batch(changed,35):
  live=graphql(tok,q,{"ids":[r["product_id"] for r in pack]})["nodes"]
  for row,p in zip(pack,live):
   base=original[row["product_id"]]
   if not p or p["id"]!=row["product_id"] or p["title"]!=row["title"] or row["sku"] not in [v["sku"] for v in p["variants"]["nodes"]]:
    skipped.append({"sku":row["sku"],"reason":"Product missing or title/SKU changed"})
    continue
   current=[x["image"]["url"] for x in p["media"]["nodes"] if x.get("image") and x["image"].get("url")]
   if current[:3]!=base["images"][:3]:
    skipped.append({"sku":row["sku"],"reason":"Photo URL changed since original snapshot"})
    continue
   verified.append((row,p))
 print("CROSS_TAG_VERIFIED",len(verified),"SKIPPED",len(skipped),flush=True)

 confirmed=[];errors=[]
 for row,p in verified:
  pid=json.dumps(row["product_id"])
  # Add MISMATCH first, then remove REVIEW only once MISMATCH succeeded.
  if TAG_M not in p["tags"]:
   add=graphql(tok,"mutation { tagsAdd(id:"+pid+",tags:[\""+TAG_M+"\"]){userErrors{field message}}}")
   err=add["tagsAdd"]["userErrors"]
   if err:
    errors.append({"sku":row["sku"],"action":"add MISMATCH","errors":err})
    continue
  if TAG_R in p["tags"]:
   rem=graphql(tok,"mutation { tagsRemove(id:"+pid+",tags:[\""+TAG_R+"\"]){userErrors{field message}}}")
   err=rem["tagsRemove"]["userErrors"]
   if err:
    errors.append({"sku":row["sku"],"action":"remove REVIEW","errors":err})
    continue
  confirmed.append(row["sku"])
  if len(confirmed)%40==0:print("UPGRADED_TAGS",len(confirmed),"/",len(verified),flush=True)
 final["shopify_crossref_upgraded"]=confirmed
 final["shopify_skipped_stale"]=skipped
 final["shopify_errors"]=errors
 (ROOT/"cross_catalog_final_report.json").write_text(json.dumps(final,ensure_ascii=False,indent=1))
 if errors:raise RuntimeError("Some cross-reference tag upgrades failed")
 print("CROSS_REF_FINAL_DONE",len(confirmed),"REVIEW->MISMATCH",flush=True)

if __name__=="__main__":main()
