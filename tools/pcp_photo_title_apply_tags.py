#!/usr/bin/env python3
"""Apply tags after all 6 Shopify OCR shards finish with complete evidence.
Only adds PCP-IMAGE-TITLE-MISMATCH or PCP-IMAGE-TITLE-REVIEW based on status;
never changes source photographs, titles, inventory, prices or unrelated tags.
Marks MATCH without tags. Strict product-ID + title/variant/photo validation.
"""
import argparse,json,os,time,glob,urllib.parse,urllib.request,urllib.error
from pathlib import Path
from collections import Counter

SHOP="72b63e-9f.myshopify.com"
API="2026-07"
TAG_M="PCP-IMAGE-TITLE-MISMATCH"
TAG_R="PCP-IMAGE-TITLE-REVIEW"
OUT=Path("photo_title_audit")
OUT.mkdir(exist_ok=True)

def tok():
 body=urllib.parse.urlencode({
   "grant_type":"client_credentials",
   "client_id":os.environ["SHOPIFY_CLIENT_ID"],
   "client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]}).encode()
 req=urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token",data=body,method="POST")
 with urllib.request.urlopen(req,timeout=45) as res:return json.load(res)["access_token"]

def graphql(token,query,variables=None):
 content=json.dumps({"query":query,"variables":variables or {}}).encode()
 for a in range(7):
  req=urllib.request.Request(f"https://{SHOP}/admin/api/{API}/graphql.json",
    data=content,headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"},method="POST")
  try:
   with urllib.request.urlopen(req,timeout=90) as r:obj=json.load(r)
   if "errors" in obj:raise RuntimeError("GraphQL errors "+str(obj["errors"])[:1000])
   return obj["data"]
  except urllib.error.HTTPError as err:
   if err.code not in (429,500,502,503,504) or a==6:raise
   time.sleep(2*(a+1))
 raise RuntimeError("Retries exhausted")

def chunk(seq,n):
 for i in range(0,len(seq),n):yield seq[i:i+n]

def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--dry-run",action="store_true")
 args=parser.parse_args()
 snapshot=json.loads((OUT/"shopify_catalog_snapshot.json").read_text(encoding="utf-8"))
 files=sorted(glob.glob("photo_title_audit/results_shard_*.json"))
 if len(files)!=6:raise RuntimeError(f"Expected exactly 6 shard files, found {len(files)}: {files}")
 rows=[]
 for f in files:rows.extend(json.loads(Path(f).read_text(encoding="utf-8")))
 if len(rows)!=len(snapshot):
  raise RuntimeError(f"Incomplete OCR results: {len(rows)} != catalog {len(snapshot)}")
 source={p["product_id"]:p for p in snapshot}
 classified={r["product_id"]:r for r in rows}
 if len(source)!=len(snapshot) or len(classified)!=len(rows) or set(source)!=set(classified):
  raise RuntimeError("Missing/duplicate or unexpected Shopify Product IDs in audit")
 allowed={"MATCH","MISMATCH","REVIEW"}
 if any(r["classification"] not in allowed for r in rows):raise RuntimeError("Invalid status")
 summary=Counter(r["classification"] for r in rows)
 print("AUDIT_ALL",len(rows),dict(summary),flush=True)

 # Full user-friendly proof file, machine-readable and CSV (no photos changed).
 with (OUT/"photo_title_results.csv").open("w",encoding="utf-8-sig",newline="") as f:
  import csv
  w=csv.writer(f,delimiter=";")
  w.writerow(["sku","product_id","title","classification","reason","title_refs","matched_refs","photo_brands","shopify_admin","photo_1","photo_2","photo_3"])
  for r in rows:
   imgs=r.get("image_urls",[])
   w.writerow([r["sku"],r["product_id"],r["title"],r["classification"],r["reason"],
    "|".join(r.get("title_refs",[])),"|".join(r.get("matched_refs",[])),
    "|".join(r.get("detected_brands",[])),
    "https://admin.shopify.com/store/72b63e-9f/products/"+r["product_id"].split("/")[-1],
    *[imgs[i] if i<len(imgs) else "" for i in range(3)]])
 if args.dry_run:
  print("DRY_RUN_DO_NOT_TAG",flush=True)
  return
 token=tok()
 q="""query($ids:[ID!]!){nodes(ids:$ids){... on Product{
    id title tags variants(first:6){nodes{sku}}
    media(first:5){nodes{... on MediaImage{image{url}}}}
  }}}"""
 to_tag=[]
 stale=[]
 for ids in chunk(sorted(source),40):
  live=graphql(token,q,{"ids":ids})["nodes"]
  if len(live)!=len(ids):raise RuntimeError("Query nodes length mismatch")
  for pid,p in zip(ids,live):
   saved=source[pid]
   if not p or p.get("id")!=pid:
    stale.append({"id":pid,"sku":saved["sku"],"reason":"PRODUCT_NOT_FOUND"})
    continue
   if saved["title"]!=p["title"] or saved["sku"] not in [v["sku"] for v in p["variants"]["nodes"]]:
    stale.append({"id":pid,"sku":saved["sku"],"reason":"TITLE_OR_SKU_CHANGED"})
    continue
   imglive=[x["image"]["url"] for x in p["media"]["nodes"] if x.get("image") and x["image"].get("url")]
   if imglive[:3]!=saved["images"][:3]:
    stale.append({"id":pid,"sku":saved["sku"],"reason":"PHOTO_CHANGED"})
    continue
   classification=classified[pid]["classification"]
   tag=TAG_M if classification=="MISMATCH" else TAG_R if classification=="REVIEW" else None
   if tag and tag not in p["tags"]:
    to_tag.append({"id":pid,"sku":saved["sku"],"tag":tag})
 print("LIVE_VALIDATED",len(source)-len(stale),"STALE_SKIP",len(stale),"TO_ADD",len(to_tag),flush=True)
 if len(stale)>len(snapshot)*0.10:raise RuntimeError("Unexpected high number of catalog changes, abort tagging")
 if summary["MATCH"] < max(12,int(len(snapshot)*.05)):
  raise RuntimeError("Too few OCR title-reference matches: potential scanner malfunction, no tags applied")
 # Separate tags for disjoint categories. Existing tags and media remain untouched.
 results={"initial_total":len(snapshot),"classified":dict(summary),
          "skipped_due_to_live_changes":stale,"tag_targets":len(to_tag),
          "tagged_successfully":[],"tag_errors":[]}
 report=OUT/"tagging_report.json"
 report.write_text(json.dumps(results,indent=1,ensure_ascii=False))
 for group in chunk(to_tag,12):
  mutations=[]
  for k,item in enumerate(group):
   mutations.append(f'a{k}:tagsAdd(id:{json.dumps(item["id"])},tags:[{json.dumps(item["tag"])}])'
      +'{userErrors{field message}}')
  rsp=graphql(token,"mutation AddAuditTags { "+" ".join(mutations)+" }")
  for k,item in enumerate(group):
   errors=rsp[f"a{k}"]["userErrors"]
   if errors: results["tag_errors"].append({"item":item,"error":errors})
   else:results["tagged_successfully"].append(item)
  if len(results["tagged_successfully"])%120<12:
   print("TAGS_PROGRESS",len(results["tagged_successfully"]),"/",len(to_tag),
         "ERRORS",len(results["tag_errors"]),flush=True)
   report.write_text(json.dumps(results,indent=1,ensure_ascii=False))
  time.sleep(.45)
 report.write_text(json.dumps(results,indent=1,ensure_ascii=False))
 if results["tag_errors"]:raise RuntimeError("Shopify tagging incomplete: "+str(results["tag_errors"][:3]))
 print("TAGS_APPLIED",len(results["tagged_successfully"]),"SHARD_TOTAL",len(snapshot),flush=True)

if __name__=="__main__":main()
