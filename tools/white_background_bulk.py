#!/usr/bin/env python3
"""PrimoCarParts FIRST PHOTO background cleanup. Shopify is the only catalog master.
Preview by default; apply updates existing MediaImage IN PLACE (same media ID and position).
Originals and white edits are saved in GitHub Actions artifacts.
"""
import argparse, csv, io, json, os, re, time, random, urllib.parse
from pathlib import Path
import requests
import numpy as np
from PIL import Image, ImageOps, ImageDraw
from rembg import remove, new_session

SHOP = "72b63e-9f.myshopify.com"
API = "2026-07"
TAG = "PCP-PHOTO-1-NOT-WHITE"
OUT = Path("white_bg_output")
OUT.mkdir(exist_ok=True)
S = requests.Session()

def token():
    r=S.post(f"https://{SHOP}/admin/oauth/access_token",data={
         "grant_type":"client_credentials",
         "client_id":os.environ["SHOPIFY_CLIENT_ID"],
         "client_secret":os.environ["SHOPIFY_CLIENT_SECRET"]},timeout=45)
    r.raise_for_status()
    return r.json()["access_token"]

def graphql(t, query, variables=None):
    for at in range(7):
        try:
            r=S.post(f"https://{SHOP}/admin/api/{API}/graphql.json",
                json={"query":query,"variables":variables or {}},
                headers={"X-Shopify-Access-Token":t},timeout=90)
            if r.status_code in (429,500,502,503,504):
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            data=r.json()
            if data.get("errors"): raise RuntimeError(str(data["errors"])[:900])
            return data["data"]
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError):
            if at==6: raise
            time.sleep(2*(at+1))
    raise RuntimeError("GraphQL failed")

def fetch_products(t):
    q="""query($after:String){products(first:100,after:$after,query:"tag:PCP-PHOTO-1-NOT-WHITE"){
      nodes{id title status tags variants(first:5){nodes{sku}}
        media(first:5){nodes{id alt mediaContentType ... on MediaImage{fileStatus image{url width height}}}}
      }pageInfo{hasNextPage endCursor}}}"""
    allp=[]; cursor=None
    while True:
        d=graphql(t,q,{"after":cursor})["products"]
        for p in d["nodes"]:
            sku=p["variants"]["nodes"][0]["sku"] if p["variants"]["nodes"] else ""
            p["sku"]=sku
            allp.append(p)
        if not d["pageInfo"]["hasNextPage"]:break
        cursor=d["pageInfo"]["endCursor"]
    if len(allp)!=322:raise RuntimeError(f"Safety: tagged products {len(allp)} not 322")
    if len({p["id"] for p in allp})!=322:raise RuntimeError("Duplicate product IDs")
    return sorted(allp,key=lambda p:p["sku"])

def download(url):
    r=S.get(url,timeout=45)
    r.raise_for_status()
    if len(r.content)>18_000_000:raise RuntimeError("Image larger than 18MB")
    if not r.headers.get("Content-Type","").startswith("image/"):
        raise RuntimeError("Shopify source not an image: "+r.headers.get("Content-Type",""))
    return r.content

def edge_score(rgb):
    ar=np.asarray(rgb.resize((256,256))).astype(np.int16)
    edge=np.concatenate([ar[:18].reshape(-1,3),ar[-18:].reshape(-1,3),
         ar[18:-18,:18].reshape(-1,3),ar[18:-18,-18:].reshape(-1,3)])
    return float(np.mean(np.min(edge,axis=1)>=238))

def modify(original,session):
    im=ImageOps.exif_transpose(Image.open(io.BytesIO(original))).convert("RGB")
    if im.width<250 or im.height<250:raise RuntimeError("Image too small")
    if max(im.size)>1600:
        z=1600/max(im.size)
        im=im.resize((int(im.width*z),int(im.height*z)),Image.Resampling.LANCZOS)
    corner_size=max(8,int(min(im.size)*.10))
    src=np.asarray(im,dtype=np.uint8)
    corners=[src[:corner_size,:corner_size].mean(),
             src[:corner_size,-corner_size:].mean(),
             src[-corner_size:,:corner_size].mean(),
             src[-corner_size:,-corner_size:].mean()]
    if max(corners)<140:
        raise RuntimeError("Tight close-up, cannot remove housing without damage")
    if edge_score(im)>.90:
        return im,None,{"skip":"ALREADY_WHITE"}
    fg=remove(im,session=session,post_process_mask=True)
    fg=fg.convert("RGBA")
    a=np.asarray(fg.getchannel("A"),dtype=np.uint8)
    solid=a>=128
    ratio=float(solid.mean())
    if not (.045<=ratio<=.90):
        raise RuntimeError(f"Unreliable foreground occupancy: {ratio:.3f}")
    y,x=np.nonzero(solid)
    if not len(x):raise RuntimeError("No foreground")
    left,right,top,bottom=x.min(),x.max(),y.min(),y.max()
    touches=float(np.mean(a[0]>220)+np.mean(a[-1]>220)+np.mean(a[:,0]>220)+np.mean(a[:,-1]>220))/4
    if touches>.25:raise RuntimeError(f"Object clipped or background detected: {touches:.3f}")
    cx,cy=float(np.mean(x))/im.width,float(np.mean(y))/im.height
    if abs(cx-.5)>.38 or abs(cy-.5)>.38:raise RuntimeError("Foreground off centre")
    white=Image.new("RGBA",im.size,(255,255,255,255))
    white.alpha_composite(fg)
    result=white.convert("RGB")
    score=edge_score(result)
    if score<.90:raise RuntimeError(f"Background not white after conversion: {score:.3f}")
    return im,result,{"mask_coverage":round(ratio,3),"edge_white":round(score,3),
        "touches":round(touches,3),"center":[round(cx,3),round(cy,3)]}

def jpeg(im):
    buf=io.BytesIO()
    im.save(buf,format="JPEG",quality=93,subsampling=0,optimize=True)
    return buf.getvalue()

def stage_image(t,filename,img):
    q="""mutation($input:[StagedUploadInput!]!){
      stagedUploadsCreate(input:$input){stagedTargets{url resourceUrl parameters{name value}}userErrors{field message}}}"""
    d=graphql(t,q,{"input":[{"filename":filename,"mimeType":"image/jpeg","resource":"IMAGE",
              "httpMethod":"POST","fileSize":str(len(img))}]})["stagedUploadsCreate"]
    if d["userErrors"]:raise RuntimeError("Staged upload creation "+str(d["userErrors"]))
    dest=d["stagedTargets"][0]
    params={v["name"]:v["value"] for v in dest["parameters"]}
    resp=requests.post(dest["url"],data=params,files={"file":(filename,img,"image/jpeg")},timeout=100)
    if resp.status_code not in (200,201,204):
        raise RuntimeError(f"Staged upload error {resp.status_code}: {resp.text[:300]}")
    return dest["resourceUrl"]

def current_first(t, product_id):
    q="""query($id:ID!){product(id:$id){id tags
    media(first:5){nodes{id ... on MediaImage{fileStatus image{url}}}}}}"""
    return graphql(t,q,{"id":product_id})["product"]

def replace_image(t,p,resource):
    orig=p["media"]["nodes"][0]
    current=current_first(t,p["id"])
    if not current or TAG not in current["tags"]:raise RuntimeError("Tag lost")
    media=current["media"]["nodes"]
    if not media or media[0]["id"]!=orig["id"] or media[0]["fileStatus"]!="READY":
        raise RuntimeError("First media ID/status changed, abort")
    old_ids=[v["id"] for v in media]
    q="""mutation($inputs:[FileUpdateInput!]!){
       fileUpdate(files:$inputs){files{id fileStatus}userErrors{field message code}}}"""
    d=graphql(t,q,{"inputs":[{"id":orig["id"],"originalSource":resource}]})["fileUpdate"]
    if d["userErrors"]:raise RuntimeError("fileUpdate "+str(d["userErrors"]))
    for i in range(20):
        time.sleep(2.5)
        changed=current_first(t,p["id"])
        first=changed["media"]["nodes"][0]
        if first["id"]!=orig["id"]:raise RuntimeError("First media ID changed")
        if [m["id"] for m in changed["media"]["nodes"]]!=old_ids:
            raise RuntimeError("Other media was reordered")
        if first["fileStatus"]=="READY":
            return first.get("image",{}).get("url","")
    raise RuntimeError("Image processing did not reach READY")

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--apply",action="store_true")
    a.add_argument("--limit",type=int,default=6)
    a.add_argument("--skus",default="")
    args=a.parse_args()
    t=token()
    prod=fetch_products(t)
    # Only apply when source exactly matches the first-image audit.
    audit={}
    with open("audit_results/VSE_PREVERJENE_FOTOGRAFIJE.csv",encoding="utf-8-sig",newline="") as fh:
        for row in csv.DictReader(fh,delimiter=";"):
            if row["slika_1_ozadje"]=="NI_BELO":audit[row["sku"]]=row
    if len(audit)!=322:raise RuntimeError("Audit membership not exactly 322")
    for p in prod:
        m=p["media"]["nodes"]
        if p["sku"] not in audit or len(m)==0 or m[0]["mediaContentType"]!="IMAGE":
            raise RuntimeError(f"Unexpected product {p['sku']}: missing image or audit entry")
        # Source URL verified per SKU just before editing; allow already-processed SKUs.
    bysku={p["sku"]:p for p in prod}
    if args.skus:
        skus=[v.strip() for v in args.skus.split(",") if v.strip()]
        assert all(v in bysku for v in skus)
        selected=[bysku[v] for v in skus]
    else:
        selected=prod[:max(0,args.limit)]
    print("MATCHING_SKUS",len(prod),"SELECTED",len(selected),"MODE", "APPLY" if args.apply else "PREVIEW",flush=True)
    model=new_session("u2net")
    results=[]
    for index,p in enumerate(selected,1):
        sku=p["sku"]
        item={"sku":sku,"title":p["title"],"product_id":p["id"],"media_id":p["media"]["nodes"][0]["id"],
              "original_url":p["media"]["nodes"][0]["image"]["url"]}
        try:
            if item["original_url"].split("?")[0]!=audit[sku]["image1"].split("?")[0]:
                raise RuntimeError("Image changed since original audit, skip safely")
            original_bytes=download(item["original_url"])
            im,white,stats=modify(original_bytes,model)
            item.update(stats)
            # Permanently retain originals and output previews inside GitHub artifact.
            (OUT/f"{sku}_original.jpg").write_bytes(jpeg(im))
            if white is None:
                item["status"]="ALREADY_WHITE"
            else:
                processed=jpeg(white)
                (OUT/f"{sku}_white.jpg").write_bytes(processed)
                if not args.apply:
                    canvas=Image.new("RGB",(im.width*2,im.height))
                    canvas.paste(im,(0,0));canvas.paste(white,(im.width,0))
                    canvas.thumbnail((1800,900))
                    canvas.save(OUT/f"{sku}_compare.jpg",quality=88)
                    item["status"]="PREVIEW"
                else:
                    resource=stage_image(t,f"PCP_{sku}_white_1.jpg",processed)
                    item["updated_url"]=replace_image(t,p,resource)
                    item["status"]="UPDATED"
        except Exception as e:
            item["status"]="FAILED"
            item["error"]=str(e)[:350]
        results.append(item)
        (OUT/"results.json").write_text(json.dumps(results,indent=2,ensure_ascii=False))
        print(f"[{index}/{len(selected)}] {sku} {item['status']} "+str(item.get("error",""))[:120],flush=True)
    counts={}
    for row in results:counts[row["status"]]=counts.get(row["status"],0)+1
    print("WHITE_BACKGROUND_SUMMARY",json.dumps(counts),flush=True)
if __name__=="__main__":
    main()
