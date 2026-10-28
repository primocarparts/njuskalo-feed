#!/usr/bin/env python3
"""Read-only Shopify image background audit. SKU and image order from live Admin API."""
import os, io, csv, json, time, re, urllib.request, urllib.parse, urllib.error
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image
import numpy as np

SHOP = '72b63e-9f.myshopify.com'
API = '2026-07'
OUT = Path('audit_results')
OUT.mkdir(exist_ok=True)
STAMP = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%SZ')

def auth():
    ident=os.environ['SHOPIFY_CLIENT_ID']; secret=os.environ['SHOPIFY_CLIENT_SECRET']
    encoded=urllib.parse.urlencode({'grant_type':'client_credentials','client_id':ident,'client_secret':secret}).encode()
    req=urllib.request.Request('https://'+SHOP+'/admin/oauth/access_token',data=encoded,method='POST')
    with urllib.request.urlopen(req,timeout=45) as r:
        return json.load(r)['access_token']

def gql(tok,query,variables):
    req=urllib.request.Request('https://'+SHOP+'/admin/api/'+API+'/graphql.json',
        data=json.dumps({'query':query,'variables':variables}).encode(),
        headers={'Content-Type':'application/json','X-Shopify-Access-Token':tok})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req,timeout=90) as resp: data=json.load(resp)
            if data.get('errors'): raise RuntimeError(str(data['errors'])[:800])
            return data['data']
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt==4: raise
            time.sleep(1.5*(attempt+1))
    raise RuntimeError('Unreachable')

def fetch_products(tok):
    query='''query($after:String){ products(first:250, after:$after, sortKey:ID){
      nodes{id title status handle totalInventory
        variants(first:10){nodes{sku}}
        images(first:3){nodes{url}}}
      pageInfo{hasNextPage endCursor}
    }}'''
    all_items=[]; cur=None
    while True:
        pg=gql(tok,query,{'after':cur})['products']
        for p in pg['nodes']:
            sku_list=[v.get('sku','').strip() for v in p['variants']['nodes'] if v.get('sku','').strip()]
            images=[im['url'] for im in p['images']['nodes'] if im.get('url')]
            for sku in sku_list:
                all_items.append({
                    'sku':sku,'title':p['title'],'status':p['status'],
                    'inventory':p['totalInventory'], 'handle':p['handle'],
                    'product_id':p['id'].rsplit('/',1)[-1],
                    'image1':images[0] if len(images)>0 else '',
                    'image3':images[2] if len(images)>2 else '',
                    'image_count_min':len(images)
                })
        print('Products/SKUs fetched:',len(all_items),flush=True)
        if not pg['pageInfo']['hasNextPage']: break
        cur=pg['pageInfo']['endCursor']
    return all_items

def picture_status(url):
    if not url: return 'MANJKA', None, ''
    delimiter='&' if '?' in url else '?'
    resized=url+delimiter+'width=320'
    for attempt in range(3):
        try:
            req=urllib.request.Request(resized,headers={'User-Agent':'PrimoCarParts-PhotoAudit/1.0'})
            with urllib.request.urlopen(req, timeout=23) as r:
                raw=r.read(6000000)
            img=Image.open(io.BytesIO(raw)).convert('RGB')
            img.thumbnail((320,320))
            arr=np.asarray(img,dtype=np.uint8)
            h,w=arr.shape[:2]
            if min(h,w)<25: raise ValueError('image too small')
            y=max(3,int(h*.09)); x=max(3,int(w*.09))
            edge=np.concatenate([
                arr[:y,:,:].reshape(-1,3),arr[-y:,:,:].reshape(-1,3),
                arr[y:-y,:x,:].reshape(-1,3),arr[y:-y,-x:,:].reshape(-1,3)
            ],axis=0).astype(np.int16)
            whites=np.all(edge>=238,axis=1)&((np.max(edge,axis=1)-np.min(edge,axis=1))<=23)
            score=round(float(np.mean(whites))*100,1)
            if score>=87: label='BELO'
            elif score>=64: label='PREVERI'
            else: label='NI_BELO'
            return label,score,''
        except Exception as e:
            if attempt==2: return 'NAPAKA',None,str(e)[:100]
            time.sleep(0.6*(attempt+1))
    return 'NAPAKA',None,'unknown'

def main():
    print('START',STAMP,flush=True)
    rows=fetch_products(auth())
    print('SKUS',len(rows),'unique',len(set(r['sku'] for r in rows)),flush=True)
    urls={r[k] for r in rows for k in ('image1','image3') if r[k]}
    print('IMAGES_TO_CHECK',len(urls),flush=True)
    outcomes={}
    with ThreadPoolExecutor(max_workers=24) as pool:
        fs={pool.submit(picture_status,u):u for u in urls}
        for i,f in enumerate(as_completed(fs),1):
            outcomes[fs[f]]=f.result()
            if i%1000==0: print('Image checks:',i,'of',len(urls),flush=True)
    output=[]
    for r in rows:
        one=outcomes.get(r['image1'],('MANJKA',None,'')) if r['image1'] else ('MANJKA',None,'')
        three=outcomes.get(r['image3'],('MANJKA',None,'')) if r['image3'] else ('MANJKA',None,'')
        r['slika_1_ozadje']=one[0];r['slika_1_belo_pct']=one[1] if one[1] is not None else ''
        r['slika_3_ozadje']=three[0];r['slika_3_belo_pct']=three[1] if three[1] is not None else ''
        r['opomba']='; '.join(('Slika 1: '+one[0]) if one[0]!='BELO' else '' for _ in [1]).strip('; ')
        if three[0]!='BELO': r['opomba']=('; '.join(filter(None,[r['opomba'],'Slika 3: '+three[0]])))
        r['shopify_link']='https://admin.shopify.com/store/72b63e-9f/products/'+r['product_id']
        r['error']= '; '.join(x for x in (one[2],three[2]) if x)
        output.append(r)
    headers=['sku','title','status','inventory','slika_1_ozadje','slika_1_belo_pct',
        'slika_3_ozadje','slika_3_belo_pct','opomba','shopify_link','image1','image3','error']
    affected=[r for r in output if r['slika_1_ozadje']!='BELO' or r['slika_3_ozadje']!='BELO']
    definitely=[r for r in output if 'NI_BELO' in (r['slika_1_ozadje'],r['slika_3_ozadje'])]
    needs_review=[r for r in output if 'PREVERI' in (r['slika_1_ozadje'],r['slika_3_ozadje'])]
    missing=[r for r in output if 'MANJKA' in (r['slika_1_ozadje'],r['slika_3_ozadje'])]
    errs=[r for r in output if 'NAPAKA' in (r['slika_1_ozadje'],r['slika_3_ozadje'])]
    def write(name,data):
        with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore',delimiter=';')
            w.writeheader();w.writerows(sorted(data,key=lambda z:z['sku']))
    write('SKU_FOTOGRAFIJE_BREZ_BELEGA_OZADJA.csv',affected)
    write('SKU_POTRJENO_NE_BELA.csv',definitely)
    write('VSE_PREVERJENE_FOTOGRAFIJE.csv',output)
    totals={'run_utc':STAMP,'sku_count':len(output),'image_urls_checked':len(urls),
        'needs_action':len(affected),'definitely_nonwhite':len(definitely),
        'borderline':len(needs_review),'missing':len(missing),'download_errors':len(errs)}
    (OUT/'POVZETEK.json').write_text(json.dumps(totals,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('AUDIT_RESULTS_JSON',json.dumps(totals,ensure_ascii=False),flush=True)
    print('NONWHITE_SKUS_BEGIN',flush=True)
    print(','.join(sorted({r['sku'] for r in definitely})),flush=True)
    print('NONWHITE_SKUS_END',flush=True)
    if errs: print('WARNING: Some images failed to download. Recheck NAPAKA rows.',flush=True)

if __name__=='__main__': main()
