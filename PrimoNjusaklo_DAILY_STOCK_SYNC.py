import os,re,json,html,urllib.request,urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from html.parser import HTMLParser

API='2026-07'; SHOP='72b63e-9f.myshopify.com'
USER_ID='3303688'; CATEGORY_ID='12813'; LIMIT=None
LOCATION_ID='13065'
OUT=Path(__file__).with_name('njuskalo.xml')
CFG=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'PrimoNjusakloSync' / 'config.json'

class Text(HTMLParser):
 def __init__(self): super().__init__(); self.a=[]
 def handle_data(self,d): self.a.append(d)
 def handle_starttag(self,t,a):
  if t in ('p','br','li','h1','h2','h3','h4'): self.a.append('\n')
def html_to_text(s):
 p=Text(); p.feed(s or ''); return re.sub(r'\n{3,}','\n\n',html.unescape(''.join(p.a))).strip()

def auth():
 cid=os.environ.get("SHOPIFY_CLIENT_ID","").strip(); sec=os.environ.get("SHOPIFY_CLIENT_SECRET","").strip()
 if not cid or not sec: raise RuntimeError("Missing GitHub Secrets: SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET")
 data=urllib.parse.urlencode({"grant_type":"client_credentials","client_id":cid,"client_secret":sec}).encode()
 req=urllib.request.Request(f"https://{SHOP}/admin/oauth/access_token",data=data,method="POST")
 with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read())["access_token"]

def gql(tok,q,var=None):
 req=urllib.request.Request(f'https://{SHOP}/admin/api/{API}/graphql.json',data=json.dumps({'query':q,'variables':var or {}}).encode(),headers={'Content-Type':'application/json','X-Shopify-Access-Token':tok})
 with urllib.request.urlopen(req,timeout=90) as r:o=json.loads(r.read())
 if o.get('errors'): raise RuntimeError(json.dumps(o['errors']))
 return o['data']

def config():
 CFG.parent.mkdir(parents=True,exist_ok=True)
 if CFG.exists(): return json.loads(CFG.read_text(encoding='utf-8'))
 print('\nFirst setup: Njuskalo requires map coordinates even for location Slovenija.')
 lat=input('Latitude for your Njuskalo seller location: ').strip(); lng=input('Longitude: ').strip()
 if not lat or not lng: raise RuntimeError('Latitude/longitude are required by Njuskalo XML specification.')
 c={'lat':lat,'lng':lng,'webshop':'https://primocarparts.com/'}; CFG.write_text(json.dumps(c,indent=2),encoding='utf-8'); return c

# Standard Shopify ECU description -> Croatian Njuškalo description.
# Product title/reference values are preserved exactly; only explanatory text/section names are translated.
def cro_desc(h):
 s=html_to_text(h)
 s=re.split(r'\n\s*(?:Worldwide Shipping|Economy\s*&?\s*Express Shipping)\s*\n',s,flags=re.I)[0].strip()
 replacements=[
  ('Original used engine control unit (ECU).','Originalni rabljeni kompjuter motora (ECU).'),
  ('Reference Numbers','Brojevi dijela / reference'),
  ('Compatible with','Kompatibilno s'),
  ('Condition','Stanje'),
  ('Used original part. The item may show normal signs of previous use. Please check all product photos carefully, as they show the actual item you will receive.',
   'Rabljeni originalni dio. Artikl može imati uobičajene tragove korištenja. Molimo pažljivo pregledajte sve fotografije jer prikazuju stvarni artikl koji ćete dobiti.'),
  ('Important Compatibility Check','Važna provjera kompatibilnosti'),
  ('Please compare all reference numbers on your original ECU with the numbers in this listing before ordering. Vehicle model, engine size and year alone are not sufficient to guarantee compatibility. Also compare the connectors, casing and product photos.',
   'Prije narudžbe usporedite sve referentne brojeve na Vašem originalnom ECU-u s brojevima u ovom oglasu. Sam model vozila, motor i godina proizvodnje nisu dovoljni za potvrdu kompatibilnosti. Također usporedite priključke, kućište i fotografije proizvoda.'),
  ('Before Ordering','Prije narudžbe'),
  ('If you are unsure whether this ECU is suitable for your vehicle, please send us your original ECU reference numbers before ordering.',
   'Ako niste sigurni odgovara li ovaj ECU Vašem vozilu, pošaljite nam referentne brojeve Vašeg originalnog ECU-a prije narudžbe.'),
  ('Programming / Immobilizer','Programiranje / imobilizator'),
  ('This is a used original ECU. Depending on the vehicle, installation may require coding, cloning, immobilizer synchronization, virginizing or transferring data from the original ECU.',
   'Ovo je rabljeni originalni ECU. Ovisno o vozilu, nakon ugradnje može biti potrebno kodiranje, kloniranje, sinkronizacija imobilizatora, virginizacija ili prijenos podataka s originalnog ECU-a.'),
  ('IMMO OFF / ECU Decoding','IMMO OFF / ECU dekodiranje'),
  ('IMMO OFF and ECU decoding services may be available for selected ECUs. Please send us a message with your ECU reference numbers to receive a quote and confirm availability.',
   'IMMO OFF i usluge dekodiranja ECU-a dostupne su za određene ECU jedinice. Pošaljite nam referentne brojeve ECU-a kako bismo provjerili dostupnost i poslali ponudu.')
 ]
 for en,hr in replacements: s=s.replace(en,hr)
 return s+'\n\nDOSTAVA ZA HRVATSKU\nDostava na adresu u Hrvatskoj: 10 EUR.\nDostava na otoke: +2 EUR.\nMoguće plaćanje pouzećem.'

def title_for_njuskalo(title):
 # ECU and ABS titles stay exactly as they are in Shopify.
 # No automatic shortening/truncation in V0.3.
 t=re.sub(r'\s+',' ',title or '').strip()
 return t

def shopify_products(tok):
 q="""query($after:String){products(first:100,after:$after,query:"status:active"){nodes{id title descriptionHtml productType variants(first:100){nodes{sku price inventoryQuantity}} images(first:10){nodes{url}}} pageInfo{hasNextPage endCursor}}}"""
 out={}; after=None
 while True:
  x=gql(tok,q,{'after':after})['products']
  for p in x['nodes']:
   if 'ECU' not in (p.get('title') or '').upper() and 'ECU' not in (p.get('productType') or '').upper(): continue
   for v in p['variants']['nodes']:
    sku=(v.get('sku') or '').strip()
    if sku: out[sku]=(p,v)
  if not x['pageInfo']['hasNextPage']: break
  after=x['pageInfo']['endCursor']
 return out

def add(parent,name,value):
 e=ET.SubElement(parent,name); e.text=str(value); return e

def make_ad(p,v):
 a=ET.Element('ad_item',{'class':'ad_simple'}); sku=v['sku'].strip()
 add(a,'user_id',USER_ID); add(a,'original_id',sku); add(a,'category_id',CATEGORY_ID); add(a,'title',title_for_njuskalo(p['title']))
 add(a,'currency_id','2'); add(a,'price',v['price']); add(a,'description',cro_desc(p.get('descriptionHtml',''))); add(a,'conditionId','20')
 add(a,'location_id',LOCATION_ID); add(a,'gmap_lng','13.6080'); add(a,'gmap_lat','45.8950'); add(a,'isApproximateLocationOnMap','1')
 add(a,'webshopLink','https://primocarparts.com/'); add(a,'internalItemCode',sku)
 imgs=[n.get('url','') for n in p['images']['nodes'] if n.get('url','')]
 if imgs:
  il=ET.SubElement(a,'image_list')
  for u in imgs[:3]: add(il,'image',u)
 return a

def main():
 if not OUT.exists(): raise RuntimeError('njuskalo.xml not found in repository root')
 tree=ET.parse(OUT); root=tree.getroot()
 if root.tag!='ad_list': raise RuntimeError('Invalid njuskalo.xml root')
 existing={}
 for ad in root.findall('ad_item'):
  sku=(ad.findtext('original_id') or '').strip()
  if sku: existing[sku]=ad
 current=shopify_products(auth())
 in_stock={sku:pv for sku,pv in current.items() if (pv[1].get('inventoryQuantity') or 0)>0}
 remove_skus=sorted(set(existing)-set(in_stock))
 for sku in remove_skus: root.remove(existing[sku])
 add_skus=sorted(set(in_stock)-set(existing))
 for sku in add_skus: root.append(make_ad(*in_stock[sku]))
 ET.indent(root,space='\t'); tree.write(OUT,encoding='UTF-8',xml_declaration=True)
 print('Existing before:',len(existing)); print('Added:',len(add_skus)); print('Removed:',len(remove_skus)); print('Final XML ads:',len(root.findall('ad_item')))
 if add_skus: print('ADDED SKUs:',', '.join(add_skus))
 if remove_skus: print('REMOVED SKUs:',', '.join(remove_skus))

if __name__=='__main__': main()
