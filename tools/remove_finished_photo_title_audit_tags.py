#!/usr/bin/env python3
"""Remove ONLY the two completed photo-title audit tags across all Shopify products.
No changes to photos, product titles, stock, SKU, price or any other tags.
Read actual product tags and verify via direct product-node queries and global exact counts.
"""
import json,os,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path

SHOP="72b63e-9f.myshopify.com"
API="2026-07"
TAGS=["PCP-IMAGE-TITLE-MISMATCH","PCP-IMAGE-TITLE-REVIEW"]
REPORT=Path("photo_title_audit_tag_removal_report.json")

def auth():
 form=urllib.parse.urlencode({"grant_type":"client_credentials",
     "client_id":os.environ["SHOPIFY_CLIENT_ID"],
     "client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]}).encode()
 req=urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token",
     data=form,method="POST")
 with urllib.request.urlopen(req,timeout=45) as r:return json.load(r)["access_token"]

def gql(token,query,variables=None):
 data=json.dumps({"query":query,"variables":variables or {}}).encode()
 for i in range(9):
  request=urllib.request.Request(f"https://{SHOP}/admin/api/{API}/graphql.json",
    data=data,method="POST",
    headers={"X-Shopify-Access-Token":token,"Content-Type":"application/json"})
  try:
   with urllib.request.urlopen(request,timeout=100) as r:body=json.load(r)
   if body.get("errors"):
    raise RuntimeError("GraphQL returned errors: "+str(body["errors"])[:1100])
   return body["data"]
  except urllib.error.HTTPError as e:
   if e.code not in (429,500,502,503,504) or i==8:raise
   time.sleep(2*(i+1))
 raise RuntimeError("Shopify retries exhausted")

def batches(items,n):
 for pos in range(0,len(items),n):
  yield items[pos:pos+n]

LIST_QUERY="""query TaggedProducts($query:String!,$after:String){
 products(first:100,query:$query,after:$after,sortKey:ID){
  nodes{id tags}
  pageInfo{hasNextPage endCursor}
 }
}"""
NODE_QUERY="""query CheckTags($ids:[ID!]!){nodes(ids:$ids){
 ... on Product{id tags}
}}"""
COUNT_QUERY="""query TagCounts{
 a:productsCount(query:"tag:PCP-IMAGE-TITLE-MISMATCH"){count precision}
 b:productsCount(query:"tag:PCP-IMAGE-TITLE-REVIEW"){count precision}
}"""

def tagged(token,tag):
 products={}
 after=None
 for page in range(120):
  data=gql(token,LIST_QUERY,{"query":"tag:"+tag,"after":after})["products"]
  for p in data["nodes"]:
   # Shopify product-search index can lag after removals.
   if tag in p["tags"]:products[p["id"]]=p
  if not data["pageInfo"]["hasNextPage"]:
   return products
  after=data["pageInfo"]["endCursor"]
 raise RuntimeError("Pagination safety limit exceeded")

def write_report(report):
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def remove(token,ids,report):
 for group in batches(sorted(ids),12):
  args=" ".join(
   "p"+str(i)+":tagsRemove(id:"+json.dumps(pid)+
   ",tags:"+json.dumps(TAGS)+"){userErrors{field message}}"
   for i,pid in enumerate(group))
  rsp=gql(token,"mutation RemoveCompletedPhotoAuditTags {"+args+"}")
  for i,pid in enumerate(group):
   errs=rsp["p"+str(i)]["userErrors"]
   if errs:report["mutation_errors"].append({"id":pid,"errors":errs})
   else:report["successful_products"].append(pid)
  if len(report["successful_products"])%120<12:
   print("PROGRESS",len(report["successful_products"]),
       "MUTATION_ERRORS",len(report["mutation_errors"]),flush=True)
   write_report(report)
  time.sleep(0.35)

def direct_verify(token,ids):
 still=[]
 for group in batches(sorted(ids),45):
  data=gql(token,NODE_QUERY,{"ids":group})["nodes"]
  for pid,p in zip(group,data):
   if p:
    remain=sorted(set(p["tags"])&set(TAGS))
    if remain:still.append({"id":pid,"tags":remain})
 return still

def count(token):
 return gql(token,COUNT_QUERY)

def main():
 token=auth()
 counts_before=count(token)
 gathered={}
 for tag in TAGS:
  found=tagged(token,tag)
  print("INITIAL",tag,len(found),flush=True)
  gathered.update(found)
 ids=set(gathered)
 report={"tags":TAGS,"initial_counts":counts_before,
  "unique_target_product_ids":len(ids),
  "successful_products":[],"mutation_errors":[],"remaining_direct":[]}
 write_report(report)
 if len(ids)>12000:
  raise RuntimeError("Abnormal count of products; abort")
 if counts_before["a"]["precision"]!="EXACT" or counts_before["b"]["precision"]!="EXACT":
  raise RuntimeError("Initial count not exact")
 if len(ids)<max(counts_before["a"]["count"],counts_before["b"]["count"]):
  raise RuntimeError("Incomplete source enumeration")
 print("UNIQUE_TARGETS",len(ids),"REMOVE_ONLY",",".join(TAGS),flush=True)
 remove(token,ids,report)
 if report["mutation_errors"]:
  write_report(report)
  raise RuntimeError("Shopify tag removal mutations failed")
 remaining=direct_verify(token,ids)
 report["remaining_direct"]=remaining
 write_report(report)
 if remaining:
  print("RETRY_DIRECT_REMAINDERS",len(remaining),flush=True)
  remove(token,[x["id"] for x in remaining],report)
  remaining=direct_verify(token,ids)
  report["remaining_direct"]=remaining
  write_report(report)
 if remaining or report["mutation_errors"]:
  raise RuntimeError("Some targeted tags remain despite retry")

 # Indexed tag searches may lag. Prefer exact count AND direct verification.
 for attempt in range(36):
  counts=count(token)
  report["final_counts"]=counts
  report["count_verification_attempts"]=attempt+1
  write_report(report)
  a,b=counts["a"]["count"],counts["b"]["count"]
  print("COUNT_VERIFICATION",attempt+1,"MISMATCH",a,"REVIEW",b,flush=True)
  if a==0 and b==0:
   report["verification"]="SUCCESS_ZERO_TAGS_FOR_BOTH"
   write_report(report)
   print("SUCCESS removed both tags from",len(ids),"distinct Shopify products",flush=True)
   return
  time.sleep(5)

 # After index catch-up delay, query actual products still carrying these tags.
 leftover={}
 for tag in TAGS:leftover.update(tagged(token,tag))
 report["leftover_live_tagged_ids"]=sorted(leftover)
 write_report(report)
 if leftover:
  raise RuntimeError("Some products still carry tags after first pass; inspect report")
 print("Zero live tags directly verified; search count index still catching up",flush=True)

if __name__=="__main__":main()
