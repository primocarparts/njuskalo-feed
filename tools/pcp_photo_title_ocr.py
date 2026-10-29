#!/usr/bin/env python3
"""Photo-vs-title catalog number audit for Primo Car Parts.
Read-only Shopify catalog/photo scan. Results contain real OCR text evidence,
never metadata/alt text as photo evidence. No Shopify mutations in this file.
"""
import argparse,csv,io,json,os,re,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
from PIL import Image,ImageOps,ImageEnhance,ImageFilter
import pytesseract

STORE="72b63e-9f.myshopify.com"
API="2026-07"
OUT=Path("photo_title_audit")
OUT.mkdir(exist_ok=True)

BRANDS={
 "ALFA":("ALFA","ALFAROMEO"),"DAEWOO":("DAEWOO",),"FIAT":("FIAT",),
 "LANCIA":("LANCIA",),"VW":("VW","VOLKSWAGEN"),"AUDI":("AUDI",),
 "SEAT":("SEAT",),"SKODA":("SKODA","ŠKODA"),"OPEL":("OPEL","VAUXHALL"),
 "CHEVROLET":("CHEVROLET",),"BMW":("BMW",),"MERCEDES":("MERCEDES","BENZ"),
 "RENAULT":("RENAULT",),"CITROEN":("CITROEN","CITROËN"),
 "PEUGEOT":("PEUGEOT",),"FORD":("FORD",),"TOYOTA":("TOYOTA",),
 "HONDA":("HONDA",),"HYUNDAI":("HYUNDAI",),"KIA":("KIA",),
 "NISSAN":("NISSAN",),"MAZDA":("MAZDA",),"SUZUKI":("SUZUKI",),
 "SUBARU":("SUBARU",),"VOLVO":("VOLVO",),"SAAB":("SAAB",),
 "MITSUBISHI":("MITSUBISHI",),"JEEP":("JEEP",),"CHRYSLER":("CHRYSLER",),
 "DODGE":("DODGE",),"PORSCHE":("PORSCHE",),"SMART":("SMART",),
 "MINI":("MINI",),"LEXUS":("LEXUS",),"DACIA":("DACIA",),
}
GROUPS=[
 {"ALFA","FIAT","LANCIA","JEEP","CHRYSLER","DODGE","OPEL","PEUGEOT","CITROEN"},
 {"VW","AUDI","SEAT","SKODA","PORSCHE"},
 {"RENAULT","DACIA","NISSAN","MITSUBISHI"},
 {"HYUNDAI","KIA"}, {"TOYOTA","LEXUS"}, {"FORD","MAZDA"},
 {"OPEL","CHEVROLET","DAEWOO"}
]
MANUFACTURER_WORDS={"BOSCH","MARELLI","MAGNETI","SIEMENS","CONTINENTAL","DELPHI",
 "DENSO","KEIHIN","VALEO","HITACHI","SAGEM","TRW","VDO","MOTOROLA"}
SESSION=requests.Session()

def shop_token():
 form=urllib.parse.urlencode({"grant_type":"client_credentials",
    "client_id":os.environ["SHOPIFY_CLIENT_ID"],"client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]}).encode()
 req=urllib.request.Request(f"https://{STORE}/admin/oauth/access_token",data=form,method="POST")
 with urllib.request.urlopen(req,timeout=40) as rsp:return json.load(rsp)["access_token"]

def gql(token,query,variables=None):
 body=json.dumps({"query":query,"variables":variables or {}}).encode()
 for attempt in range(8):
  req=urllib.request.Request(f"https://{STORE}/admin/api/{API}/graphql.json",data=body,
    headers={"Content-Type":"application/json","X-Shopify-Access-Token":token},method="POST")
  try:
   with urllib.request.urlopen(req,timeout=90) as rsp: data=json.load(rsp)
   if data.get("errors"):raise RuntimeError(str(data["errors"])[:900])
   return data["data"]
  except urllib.error.HTTPError as e:
   if e.code not in (429,500,502,503,504) or attempt==7:raise
   time.sleep((attempt+1)*2)
 raise RuntimeError("GraphQL retry failed")

def export_catalog():
 tok=shop_token()
 q="""query($after:String){
   products(first:100,after:$after){
      nodes{id title status tags variants(first:5){nodes{sku}}
         media(first:5){nodes{id mediaContentType ... on MediaImage{image{url width height}}}}
      }pageInfo{hasNextPage endCursor}
   }
 }"""
 data=[];after=None
 while True:
  p=gql(tok,q,{"after":after})["products"]
  for prod in p["nodes"]:
   variants=prod["variants"]["nodes"]
   sku=variants[0]["sku"] if variants else ""
   pictures=[x["image"]["url"] for x in prod["media"]["nodes"]
            if x.get("mediaContentType")=="IMAGE" and x.get("image") and x["image"].get("url")]
   data.append({"product_id":prod["id"],"sku":sku,"title":prod["title"],
         "status":prod["status"],"images":pictures[:3]})
  after=p["pageInfo"]["endCursor"]
  if not p["pageInfo"]["hasNextPage"]:break
  if len(data)>9000:raise RuntimeError("Unexpected catalog size")
 (OUT/"shopify_catalog_snapshot.json").write_text(json.dumps(data,ensure_ascii=False,indent=1))
 print("CATALOG_EXPORTED",len(data),"WITH_PHOTOS",sum(bool(p["images"]) for p in data),flush=True)
 return data

def norm(x):
 return re.sub(r"[^A-Z0-9]","",x.upper()).replace("O","0").replace("I","1")
def letters_norm(x):return re.sub(r"[^A-Z0-9]","",x.upper())
def car_brands(text):
 s=" "+re.sub("[^A-Z0-9]+"," ",text.upper())+" "
 hits=set()
 for b,aliases in BRANDS.items():
  for alias in aliases:
   if re.search(r"(?<![A-Z0-9])"+re.escape(alias)+r"(?![A-Z0-9])",s):
    hits.add(b);break
 return hits

def refs(title):
 # Product numbers are generally slash-delimited. Exclude brand/maker strings,
 # ECU variants, vehicle model/version and short generic terms.
 pieces=title.split("/")
 txt=" ".join(pieces[1:]) if len(pieces)>1 else title
 candidates=re.findall(r"[A-Za-z0-9][A-Za-z0-9._\-+]{5,}",txt)
 refs=set()
 for token in candidates:
  clean=letters_norm(token)
  if len(clean)<7 or sum(c.isdigit() for c in clean)<5:continue
  if clean in MANUFACTURER_WORDS:continue
  # Don't require a Bosch prefix: Toyota/Honda/Fiat OE part codes count too.
  refs.add(norm(token))
 return sorted(refs)

def download_image(url):
 url=url+"&width=1600" if "?" in url else url+"?width=1600"
 for attempt in range(3):
  try:
   r=SESSION.get(url,timeout=25,headers={"User-Agent":"Mozilla/5.0 (compatible; PCP-CatalogAudit/1.0)"})
   r.raise_for_status()
   if len(r.content)>20_000_000:raise RuntimeError("image too large")
   im=ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
   if max(im.size)>1600:
    im.thumbnail((1600,1600))
   return im
  except (requests.RequestException,OSError):
   if attempt==2:raise
   time.sleep(0.4*(attempt+1))

def ocr_single(im,extra=False):
 # LANCZOS upscale improves small ECU label text; avoid larger than 2000px
 if max(im.size)<1500:
  scale=min(2.4,1600/max(im.size))
  im=im.resize((max(1,int(im.width*scale)),max(1,int(im.height*scale))),Image.Resampling.LANCZOS)
 gray=ImageOps.grayscale(im)
 gray=ImageOps.autocontrast(gray,cutoff=1)
 gray=ImageEnhance.Contrast(gray).enhance(1.3)
 config="--oem 1 --psm 11"
 first=pytesseract.image_to_string(gray,lang="eng",config=config,timeout=25)
 if not extra:return first
 second=pytesseract.image_to_string(gray,lang="eng",config="--oem 1 --psm 6",timeout=25)
 return first+"\n"+second

def ngrams(text):
 # Tesseract often inserts gaps into a printed 10-digit Bosch number.
 # Extract individual tokens and concat up to 4 consecutive OCR words.
 words=[letters_norm(x) for x in re.findall(r"[A-Za-z0-9]+",text.upper())]
 words=[w for w in words if w]
 found=set()
 for i in range(len(words)):
  n=0
  for j in range(i,min(len(words),i+4)):
   n+=len(words[j])
   if n>21:break
   if 7<=n<=19:
    candidate=norm("".join(words[i:j+1]))
    if sum(c.isdigit() for c in candidate)>=5:
     found.add(candidate)
 return found

def equivalent_groups(a,b):
 if a==b:return True
 return any(a in g and b in g for g in GROUPS)

def photo_title_check(prod):
 row={"product_id":prod["product_id"],"sku":prod["sku"],"title":prod["title"],
      "status":prod["status"],"image_urls":prod["images"],"title_refs":refs(prod["title"]),
      "ocr_images":[],"classification":"","reason":"","matched_refs":[],"detected_brands":[]}
 titlebrands=car_brands(prod["title"].split("/")[0])
 seen_brands=set()
 seen_nums=set()
 errors=[]
 for index,url in enumerate(prod["images"][:3],1):
  try:
   im=download_image(url)
   # OCR at least image 1+2, image 3 may contain SKU label but no auto-match from filenames.
   raw=ocr_single(im,extra=False)
   # Second OCR pass on image 2 for small dense reference labels if no initial match
   extracted=ngrams(raw)
   candidates=set(row["title_refs"])
   match=candidates&extracted
   if not match and index<=2 and candidates:
    raw+="\n"+ocr_single(im,extra=True)
    extracted=ngrams(raw)
    match=candidates&extracted
   foundbrands=car_brands(raw)
   seen_brands|=foundbrands
   seen_nums|=extracted
   row["ocr_images"].append({"image_index":index,"ocr_text":raw[:5500],
       "reference_candidates":sorted(extracted)[:90],
       "brands":sorted(foundbrands),"matched_refs":sorted(match)})
  except Exception as e:
   errors.append(f"Photo {index}: {type(e).__name__}: {str(e)[:150]}")
   row["ocr_images"].append({"image_index":index,"error":str(e)[:160]})
 matched=set(row["title_refs"])&seen_nums
 foreign={b for b in seen_brands
      if titlebrands and not any(equivalent_groups(b,t) for t in titlebrands)}
 # Brand printed by OEM can't prove a mismatch when photo has a matching ref.
 if matched:
  row["classification"]="MATCH"
  row["reason"]="At least one title catalog/OE reference appears in actual photo OCR"
 elif foreign and titlebrands:
  row["classification"]="MISMATCH"
  row["reason"]="Contradicting vehicle brand on photo: "+",".join(sorted(foreign))+" vs title: "+",".join(sorted(titlebrands))
 elif not prod["images"]:
  row["classification"]="REVIEW"
  row["reason"]="No photos to verify"
 elif not row["title_refs"]:
  row["classification"]="REVIEW"
  row["reason"]="Title lacks a suitable catalog/OE reference for comparison"
 elif errors and len(errors)>=len(prod["images"][:3]):
  row["classification"]="REVIEW"
  row["reason"]="All photo downloads/OCR reads failed"
 else:
  row["classification"]="REVIEW"
  row["reason"]="No sufficiently reliable exact catalog number match from photos"
 row["matched_refs"]=sorted(matched)
 row["detected_brands"]=sorted(seen_brands)
 row["title_brands"]=sorted(titlebrands)
 row["errors"]=errors
 return row

def main():
 ap=argparse.ArgumentParser()
 ap.add_argument("--export",action="store_true")
 ap.add_argument("--pilot",action="store_true")
 ap.add_argument("--shard",type=int,default=0)
 ap.add_argument("--shards",type=int,default=1)
 ap.add_argument("--max-products",type=int,default=0)
 ap.add_argument("--snapshot",default=str(OUT/"shopify_catalog_snapshot.json"))
 args=ap.parse_args()
 if args.export:
  data=export_catalog()
  if not args.pilot:return
 else:
  data=json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
 if args.pilot:
  # Expected incorrect item, known good baseline, and randomized catalog samples.
  by_sku={p["sku"]:p for p in data}
  baseline=["B32-20","B01-02","B02-06","B02-10","B49-01","C64-07","D33-10"]
  selected=[by_sku[s] for s in baseline if s in by_sku]
  selected+=data[::max(1,len(data)//21)][:23]
  unique={p["product_id"]:p for p in selected}
  data=list(unique.values())
 else:
  import hashlib
  data=[p for p in data if int.from_bytes(hashlib.sha1(p["product_id"].encode()).digest()[:8],"big")%args.shards==args.shard]
 if args.max_products:data=data[:args.max_products]
 print("SCAN_SELECTED",len(data),"SHARD",args.shard,"OF",args.shards,flush=True)
 results=[]
 for n,p in enumerate(data,1):
  try:r=photo_title_check(p)
  except Exception as e:
   r={"product_id":p["product_id"],"sku":p["sku"],"title":p["title"],
      "classification":"REVIEW","reason":"Unexpected OCR error: "+str(e)[:200],
      "matched_refs":[],"detected_brands":[],"image_urls":p["images"]}
  results.append(r)
  if n%20==0 or (args.pilot and n<=10):
   print("SCANNED",n,"/",len(data),dict(Counter(x["classification"] for x in results)),flush=True)
   (OUT/f"results_shard_{args.shard}.json").write_text(json.dumps(results,ensure_ascii=False,indent=1))
 (OUT/f"results_shard_{args.shard}.json").write_text(json.dumps(results,ensure_ascii=False,indent=1))
 for r in results:
  if args.pilot:print("PILOT",r["sku"],r["classification"],r["reason"],"MATCH",r.get("matched_refs"),"BRANDS",r.get("detected_brands"),flush=True)
 print("FINISHED",len(results),dict(Counter(x["classification"] for x in results)),flush=True)

if __name__=="__main__":main()
