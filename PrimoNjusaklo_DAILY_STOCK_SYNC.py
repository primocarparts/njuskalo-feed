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

# Standardised Croatian translations for Shopify SEO description templates.
# The optional DEEPL_API_KEY covers new English sentences not yet listed here.
# References, SKU codes and vehicle/model names are kept as supplied by Shopify.
CROATIAN_HEADINGS={
 'Reference Numbers':'Brojevi dijelova / reference',
 'Part Numbers':'Brojevi dijelova',
 'OEM Part Numbers':'OEM brojevi dijelova',
 'OE Numbers':'OE brojevi',
 'Compatible with':'Kompatibilno s vozilima',
 'Vehicle Compatibility':'Kompatibilnost s vozilima',
 'Vehicle Fitment':'Odgovarajuća vozila',
 'Compatibility':'Kompatibilnost',
 'Condition':'Stanje',
 'Important Compatibility Check':'Važna provjera kompatibilnosti',
 'Before Ordering':'Prije narudžbe',
 'Programming / Immobilizer':'Programiranje / imobilizator',
 'IMMO OFF / ECU Decoding':'IMMO OFF / ECU dekodiranje',
 'Shipping':'Dostava',
 'Shipping Information':'Informacije o dostavi',
 'Description':'Opis',
 'Important':'Važno',
}
CROATIAN_SENTENCES=[
 ('Original used engine control unit (ECU) from Primo Car Parts. Photos show the actual used item offered for sale.',
  'Originalni rabljeni kompjuter motora (ECU) iz Primo Car Parts. Fotografije prikazuju stvarni rabljeni dio koji se prodaje.'),
 ('Original used engine control unit (ECU).',
  'Originalni rabljeni kompjuter motora (ECU).'),
 ('Original used ECU (engine control unit).',
  'Originalni rabljeni kompjuter motora (ECU).'),
 ('Used original part. The item may show normal signs of previous use. Please check all product photos carefully, as they show the actual item you will receive.',
  'Rabljeni originalni dio. Artikl može imati uobičajene tragove korištenja. Pažljivo pregledajte sve fotografije jer prikazuju stvarni artikl koji ćete dobiti.'),
 ('Used original part. Normal cosmetic wear may be present. Inspect the actual item photos before purchase.',
  'Rabljeni originalni dio. Mogući su uobičajeni kozmetički tragovi korištenja. Prije kupnje pregledajte fotografije stvarnog artikla.'),
 ('Before ordering, compare the manufacturer and OEM reference numbers, connectors and casing to your original part. Vehicle make, model or engine alone is not enough to confirm interchangeability.',
  'Prije narudžbe usporedite proizvođačke i OEM referentne brojeve, priključke i kućište sa svojim originalnim dijelom. Samo marka, model ili motor vozila nisu dovoljni za potvrdu zamjenjivosti.'),
 ('Please compare all reference numbers on your original ECU with the numbers in this listing before ordering. Vehicle model, engine size and year alone are not sufficient to guarantee compatibility. Also compare the connectors, casing and product photos.',
  'Prije narudžbe usporedite sve referentne brojeve na svojem originalnom ECU-u s brojevima u ovom oglasu. Sam model vozila, obujam motora i godina proizvodnje nisu dovoljni za potvrdu kompatibilnosti. Usporedite i priključke, kućište te fotografije proizvoda.'),
 ('Depending on the vehicle, installing a used ECU may require coding, cloning, immobilizer synchronization or data transfer from the original ECU.',
  'Ovisno o vozilu, ugradnja rabljenog ECU-a može zahtijevati kodiranje, kloniranje, sinkronizaciju imobilizatora ili prijenos podataka s originalnog ECU-a.'),
 ('This is a used original ECU. Depending on the vehicle, installation may require coding, cloning, immobilizer synchronization, virginizing or transferring data from the original ECU.',
  'Ovo je rabljeni originalni ECU. Ovisno o vozilu, ugradnja može zahtijevati kodiranje, kloniranje, sinkronizaciju imobilizatora, virginizaciju ili prijenos podataka s originalnog ECU-a.'),
 ('If you are unsure whether this ECU is suitable for your vehicle, please send us your original ECU reference numbers before ordering.',
  'Ako niste sigurni odgovara li ovaj ECU vašem vozilu, prije narudžbe pošaljite nam referentne brojeve svojeg originalnog ECU-a.'),
 ('IMMO OFF and ECU decoding services may be available for selected ECUs. Please send us a message with your ECU reference numbers to receive a quote and confirm availability.',
  'IMMO OFF i usluge dekodiranja ECU-a mogu biti dostupne za odabrane ECU jedinice. Pošaljite nam referentne brojeve ECU-a kako bismo provjerili dostupnost i poslali ponudu.'),
 ('Photos show the actual item offered for sale.',
  'Fotografije prikazuju stvarni artikl koji se prodaje.'),
 ('Photos show the actual used item offered for sale.',
  'Fotografije prikazuju stvarni rabljeni artikl koji se prodaje.'),
 ('Please compare the reference numbers before ordering.',
  'Prije narudžbe usporedite referentne brojeve.'),
 ('Please check part numbers and photos before ordering.',
  'Prije narudžbe provjerite brojeve dijela i fotografije.'),
 ('Worldwide Shipping','Dostava diljem svijeta'),
 ('Economy & Express Shipping','Standardna i ekspresna dostava'),
 ('Used original part','Rabljeni originalni dio'),
 ('Original used part','Originalni rabljeni dio'),
]
CROATIAN_SHIPPING=(
 '\n\nDOSTAVA ZA HRVATSKU\n'
 'Dostava na adresu u Hrvatskoj: 10 EUR.\n'
 'Dostava na otoke: +2 EUR.\n'
 'Moguće plaćanje pouzećem.'
)
ENGLISH_PROSE=re.compile(
 r'\b(?:the|this|that|before|ordering|please|compare|used|original|engine|'
 r'control|photos|vehicle|depending|installing|coding|cloning|part|parts|'
 r'item|purchase|shipping|wear|inspect|require|numbers|compatible|'
 r'available|contact|product|condition|check|manufacturer)\b',
 re.I
)
UNTRANSLATED_ENGLISH=set()

TRANSLATION_CACHE_FILE=Path(__file__).with_name('njuskalo_translation_cache.json')
TRANSLATION_CACHE=None
TRANSLATION_CACHE_CHANGED=False

def deepl_translate(text):
 global TRANSLATION_CACHE,TRANSLATION_CACHE_CHANGED
 if TRANSLATION_CACHE is None:
  try:
   TRANSLATION_CACHE=json.loads(TRANSLATION_CACHE_FILE.read_text(encoding='utf-8'))
  except (OSError,ValueError):
   TRANSLATION_CACHE={}
 if text in TRANSLATION_CACHE: return TRANSLATION_CACHE[text]
 key=os.environ.get('DEEPL_API_KEY','').strip()
 if not key: return None
 api=os.environ.get('DEEPL_API_URL','').strip()
 if not api:
  api='https://api-free.deepl.com/v2/translate' if key.endswith(':fx') else 'https://api.deepl.com/v2/translate'
 payload={'text':[text],'source_lang':'EN','target_lang':'HR','preserve_formatting':True}
 req=urllib.request.Request(api,data=json.dumps(payload).encode('utf-8'),headers={
  'Authorization':'DeepL-Auth-Key '+key,'Content-Type':'application/json'
 },method='POST')
 try:
  with urllib.request.urlopen(req,timeout=30) as response:
   out=json.loads(response.read())['translations'][0]['text']
  TRANSLATION_CACHE[text]=out
  TRANSLATION_CACHE_CHANGED=True
  return out
 except Exception as exc:
  print('WARNING: DeepL translation failed:',str(exc)[:180])
  return None

def save_translation_cache():
 if TRANSLATION_CACHE_CHANGED:
  TRANSLATION_CACHE_FILE.write_text(
   json.dumps(TRANSLATION_CACHE,ensure_ascii=False,sort_keys=True,indent=2)+'\n',
   encoding='utf-8'
  )

def local_croatian_translation(text):
 for english,croatian in sorted(CROATIAN_SENTENCES,key=lambda pair:len(pair[0]),reverse=True):
  text=re.sub(re.escape(english),lambda m:croatian,text,flags=re.I)
 for english,croatian in sorted(CROATIAN_HEADINGS.items(),key=lambda pair:len(pair[0]),reverse=True):
  text=re.sub(r'(?m)^[ \t]*'+re.escape(english)+r'[ \t]*$',lambda m:croatian,text,flags=re.I)
 return text

def translate_unknown_lines(text):
 lines=[]
 for line in text.split('\n'):
  # Technical identifiers, OEM references and vehicle compatibility lists
  # must not be sent to automatic translation.
  if len(ENGLISH_PROSE.findall(line))>=2:
   translated=deepl_translate(line)
   if translated:
    line=translated
   else:
    UNTRANSLATED_ENGLISH.add(line[:160])
  lines.append(line)
 return '\n'.join(lines)

def cro_desc(h):
 s=html_to_text(h)
 # Shopify's shipping section is replaced with the agreed Croatian wording.
 s=re.split(r'(?im)^\s*(?:Worldwide Shipping|Economy\s*&?\s*Express Shipping|'
            r'Shipping Information|DOSTAVA ZA HRVATSKU)\s*$',s,maxsplit=1)[0].strip()
 s=local_croatian_translation(s)
 s=translate_unknown_lines(s)
 return s.rstrip()+CROATIAN_SHIPPING

# Non-ECU names are localised, without modifying OEM / Bosch references.
# ECU titles must remain byte-for-byte identical to Shopify (besides whitespace).
TITLE_CROATIAN_TERMS=[
 (r'\bAIR\s+FILTER\b','FILTER ZRAKA'),
 (r'\bOIL\s+FILTER\b','FILTER ULJA'),
 (r'\bFUEL\s+FILTER\b','FILTER GORIVA'),
 (r'\bCABIN\s+FILTER\b','FILTER KABINE'),
 (r'\bTHROTTLE\s+BODY\b','KUĆIŠTE LEPTIRA GASA'),
 (r'\bENGINE\s+CONTROL\s+MODULE\b','UPRAVLJAČKA JEDINICA MOTORA'),
 (r'\bENGINE\s+CONTROL\s+UNIT\b','UPRAVLJAČKA JEDINICA MOTORA'),
 (r'\bABS\s+PUMP\b','ABS PUMPA'),
 (r'\bAIRBAG\s+MODULE\b','MODUL ZRAČNIH JASTUKA'),
 (r'\bHEADLIGHT\b','PREDNJE SVJETLO'),
 (r'\bTAIL\s+LIGHT\b','STRAŽNJE SVJETLO'),
 (r'\bALTERNATOR\b','ALTERNATOR'),
 (r'\bSTARTER\s+MOTOR\b','ELEKTROPOKRETAČ'),
 (r'\bCONTROL\s+MODULE\b','UPRAVLJAČKI MODUL'),
 (r'\bSENSOR\b','SENZOR'),
 (r'\bUSED\b','RABLJENO'),
]

def title_for_njuskalo(title):
 t=re.sub(r'\s+',' ',title or '').strip()
 if re.search(r'\bECU\b',t,flags=re.I): return t
 for english,croatian in TITLE_CROATIAN_TERMS:
  t=re.sub(english,lambda m:croatian,t,flags=re.I)
 # Translate previously unknown non-ECU wording if an API key is configured.
 # Never translate slash-delimited OEM / Bosch part-number segments.
 pieces=re.split(r'(\s+/\s+)',t)
 for index in range(0,len(pieces),2):
  piece=pieces[index]
  if len(ENGLISH_PROSE.findall(piece))>=2 and not re.search(r'\d{6,}',piece):
   translated=deepl_translate(piece)
   if translated: pieces[index]=translated
 return ''.join(pieces)

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
 # Refresh descriptions and non-ECU titles on already published items.
 # Do not change existing prices, SKU identities, images or stock logic.
 updated_descriptions=0
 updated_titles=0
 for sku in sorted(set(in_stock)&set(existing)):
  p,v=in_stock[sku]
  ad=existing[sku]
  wanted_title=title_for_njuskalo(p['title'])
  wanted_desc=cro_desc(p.get('descriptionHtml',''))
  title_node=ad.find('title')
  desc_node=ad.find('description')
  if title_node is None: title_node=ET.SubElement(ad,'title')
  if desc_node is None: desc_node=ET.SubElement(ad,'description')
  if title_node.text!=wanted_title:
   title_node.text=wanted_title
   updated_titles+=1
  if desc_node.text!=wanted_desc:
   desc_node.text=wanted_desc
   updated_descriptions+=1
 if UNTRANSLATED_ENGLISH:
  print('WARNING: Unknown English lines:',len(UNTRANSLATED_ENGLISH),
        '- add DEEPL_API_KEY for fully automatic translation of new templates.')
  for sample in sorted(UNTRANSLATED_ENGLISH)[:5]: print('UNTRANSLATED:',sample)
 save_translation_cache()
 ET.indent(root,space='\t'); tree.write(OUT,encoding='UTF-8',xml_declaration=True)
 print('Existing before:',len(existing)); print('Added:',len(add_skus)); print('Removed:',len(remove_skus)); print('Updated descriptions:',updated_descriptions); print('Updated titles:',updated_titles); print('Final XML ads:',len(root.findall('ad_item')))
 if add_skus: print('ADDED SKUs:',', '.join(add_skus))
 if remove_skus: print('REMOVED SKUs:',', '.join(remove_skus))

if __name__=='__main__': main()
