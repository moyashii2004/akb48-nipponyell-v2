import json,re,hashlib,time,datetime,urllib.parse,urllib.robotparser
from pathlib import Path
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
SOURCES=json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
OUT=ROOT/'data';OUT.mkdir(exist_ok=True)
HEAD={'User-Agent':'AKB48-NipponYell-PersonalTracker/1.0 (public pages only)'}
KEYS=('AKB48','ニッポンエール','ニッポンエール号','ご当地グミ')
PREF='北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄'.split()
def read(name,default):
 p=OUT/name
 try:return json.loads(p.read_text(encoding='utf-8'))
 except (FileNotFoundError,ValueError):return default
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def allowed(url):
 try:
  p=urllib.parse.urlsplit(url)
  if p.scheme!='https':return False
  rp=urllib.robotparser.RobotFileParser();rp.set_url(f'{p.scheme}://{p.netloc}/robots.txt');rp.read()
  return rp.can_fetch(HEAD['User-Agent'],url)
 except Exception:return False

def relevant(t):return any(k.lower() in t.lower() for k in KEYS)
def classify(text):
 dates=list(dict.fromkeys(re.findall(r'(?:20\d{2}[年/.-])?\s*\d{1,2}\s*[月/.-]\s*\d{1,2}\s*日?',text)))[:6]
 prefs=[p for p in PREF if p in text]
 return {'dates_mentioned':dates,'prefectures_mentioned':prefs,'members':[], 'status':'要確認'}

def main():
 now=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds')
 prev={x['url']:x for x in read('candidates.json',[])};found={};errors=[]
 for s in SOURCES:
  url=s['url'];kind=s.get('type','website')
  if not s.get('enabled',True):continue
  if kind=='website':
   if not allowed(url):errors.append({'source':url,'error':'robots.txt disallows or unavailable'});continue
   try:
    r=requests.get(url,headers=HEAD,timeout=18);r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    for tag in soup(['script','style','nav','footer']):tag.decompose()
    for a in soup.find_all('a',href=True):
     href=urllib.parse.urljoin(url,a['href']);host=urllib.parse.urlsplit(href).netloc
     if host!=urllib.parse.urlsplit(url).netloc:continue
     title=a.get_text(' ',strip=True)[:220]
    
     if not relevant(title+' '+href):
      continue
     try:
      if allowed(href):
       article = requests.get(
        href, headers=HEAD, timeout=18
       )
       article.raise_for_status()
       article_soup = BeautifulSoup(
        article.text, 'html.parser'
       )
       for element in article_soup(
        ['script', 'style', 'nav', 'footer']
       ):
        element.decompose()
       article_text = article_soup.get_text(
        ' ', strip=True
       )[:12000]
      else:
       article_text = title
     except requests.RequestException:
      article_text = title

     found[href]={'title':title or href,'url':href,'source_type':'JA公式サイト','source':url,'first_seen':prev.get(href,{}).get('first_seen',now),'last_seen':now,**classify(article_text)
   except Exception as e:errors.append({'source':url,'error':str(e)[:250]})
  elif kind=='bluesky':
   try:
    q=s.get('query','ニッポンエール AKB48')
    r=requests.get('https://api.bsky.app/xrpc/app.bsky.feed.searchPosts',params={'q':q,'sort':'latest','limit':50},headers=HEAD,timeout=18);r.raise_for_status()
    for item in r.json().get('posts',[]):
     handle=item.get('author',{}).get('handle','');uri=item.get('uri','');text=item.get('record',{}).get('text','')
     if not relevant(text):continue
     if s.get('allowed_handles') and handle not in s['allowed_handles']:continue
     post_id=uri.rsplit('/',1)[-1];link=f'https://bsky.app/profile/{handle}/post/{post_id}'
     found[link]={'title':text[:220],'url':link,'source_type':'Bluesky公式アカウント（登録制）','source':handle,'first_seen':prev.get(link,{}).get('first_seen',now),'last_seen':now,**classify(text)}
   except Exception as e:errors.append({'source':url,'error':str(e)[:250]})
  time.sleep(.7)
 # Preserve past discoveries and mark not observed, rather than deleting.
 merged={**prev,**found}
 save('candidates.json',sorted(merged.values(),key=lambda x:x.get('first_seen',''),reverse=True))
 history=read('history.json',[])
 for url,x in found.items():
  if url not in prev:history.insert(0,{'at':now,'type':'新規候補','title':x['title'],'url':url})
 save('history.json',history[:500])
 save('status.json',{'last_run':now,'sources_configured':len(SOURCES),'new_candidates':sum(u not in prev for u in found),'candidates_total':len(merged),'errors':errors})
 print(f'Checked {len(SOURCES)} sources, found {len(found)} links, errors {len(errors)}')
if __name__=='__main__':main()
