"""Public HTML story reader with numeric pagination; blocks private network URLs."""
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

MAX_PAGES = 60
MAX_CHARS = 140000

class StoryFetchError(ValueError):
    pass

def public_url(value):
    if not isinstance(value,str) or len(value)>2048:
        raise StoryFetchError('Adres geçersiz.')
    url=urldefrag(value.strip())[0]
    p=urlparse(url)
    if p.scheme not in ('https','http') or not p.hostname or p.username or p.password or (p.port and p.port not in (80,443)):
        raise StoryFetchError('Yalnızca herkese açık HTTP(S) adresleri kullanılabilir.')
    try:
        addresses=socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80))
    except (OSError,ValueError) as exc:
        raise StoryFetchError('Alan adı çözümlenemedi.') from exc
    for entry in addresses:
        if not ipaddress.ip_address(entry[4][0]).is_global:
            raise StoryFetchError('Yerel/özel ağ adresi engellendi.')
    return url

def get_html(url,session=None):
    session=session or requests.Session()
    current=public_url(url)
    for _ in range(5):
        with session.get(current,headers={'User-Agent':'FictionStudioComic/1.0','Accept':'text/html'},timeout=(8,20),allow_redirects=False,stream=True) as response:
            if response.status_code in (301,302,303,307,308):
                target=response.headers.get('Location')
                if not target:
                    raise StoryFetchError('Boş yönlendirme.')
                target=public_url(urljoin(current,target))
                if urlparse(target).hostname!=urlparse(current).hostname:
                    raise StoryFetchError('Farklı siteye yönlendirme engellendi.')
                current=target
                continue
            if response.status_code!=200:
                raise StoryFetchError('Sayfa açılamadı: HTTP '+str(response.status_code))
            if not any(t in response.headers.get('Content-Type','').lower() for t in ('text/html','application/xhtml+xml')):
                raise StoryFetchError('Adres HTML sayfası değil.')
            data=bytearray()
            for chunk in response.iter_content(chunk_size=32768):
                data.extend(chunk)
                if len(data)>2000000:
                    raise StoryFetchError('Tek sayfa boyut sınırı aşıldı.')
            response.encoding=response.apparent_encoding or 'utf-8'
            return current,data.decode(response.encoding,errors='replace')
    raise StoryFetchError('Çok fazla yönlendirme.')

def extract_story(html_text):
    soup=BeautifulSoup(html_text,'html.parser')
    for el in soup.select('script,style,noscript,header,footer,nav,aside,form,iframe,button,svg'):
        el.decompose()
    preferred=[soup.select_one(css) for css in ('[itemprop="articleBody"]','.entry-content','.chapter-content','.chapter-body','.post-content','#chapter-content','.reading-content','article','main')]
    preferred=[el for el in preferred if el and len(el.get_text(' ',strip=True))>250]
    content=max(preferred,key=lambda el:len(el.get_text(' ',strip=True)),default=soup.body or soup)
    for el in content.select('.related,.share,.advert,.ads,.comments,.pagination,.page-numbers'):
        el.decompose()
    text=re.sub(r'\n{3,}','\n\n',re.sub(r'[ \t\xa0]+',' ',content.get_text('\n',strip=True))).strip()
    if len(text)<120:
        raise StoryFetchError('Metin bulunamadı; sayfa JavaScript veya giriş isteyebilir.')
    return text

def discover_pages(html_text,current_url,visited,first_host):
    soup=BeautifulSoup(html_text,'html.parser')
    choices=[]
    for a in soup.select('a[href]'):
        raw=a.get('href','').strip()
        if not raw or raw.startswith(('#','javascript:','mailto:')):
            continue
        target=urldefrag(urljoin(current_url,raw))[0]
        p=urlparse(target)
        if p.scheme not in ('http','https') or p.hostname!=first_host or target in visited:
            continue
        label=a.get_text(' ',strip=True)
        rel=' '.join(a.get('rel',[]))
        if re.fullmatch(r'\d{1,3}',label):
            n=int(label)
            if n>1:
                choices.append((0,n,target))
        elif 'next' in rel or label.lower() in ('next','sonraki','sonraki sayfa','devam','›','»'):
            choices.append((1,10000,target))
    return sorted(choices)

def collect_links(start_links,max_pages=MAX_PAGES,on_page=None,fetch=get_html):
    if not isinstance(start_links,list) or not 1<=len(start_links)<=25:
        raise StoryFetchError('1–25 başlangıç linki girin.')
    if not 1<=max_pages<=MAX_PAGES:
        raise StoryFetchError('Sayfa sınırı geçersiz.')
    visited=set()
    texts=[]
    total=0
    for start in start_links:
        initial=public_url(start)
        queue=[initial]
        host=urlparse(initial).hostname
        while queue and len(visited)<max_pages:
            url=queue.pop(0)
            if url in visited:
                continue
            safe=public_url(url)
            if urlparse(safe).hostname!=host:
                continue
            final,html_text=fetch(safe)
            if urlparse(final).hostname!=host:
                raise StoryFetchError('Başka siteye geçiş engellendi.')
            if final in visited:
                continue
            visited.add(final)
            story=extract_story(html_text)
            if story not in texts:
                total+=len(story)
                if total>MAX_CHARS:
                    raise StoryFetchError('Metin uzunluğu sınırı aşıldı.')
                texts.append(story)
                if on_page:
                    on_page(len(texts),final)
            for _,_,candidate in discover_pages(html_text,final,visited,host):
                if candidate not in visited and candidate not in queue:
                    queue.append(candidate)
        if queue and len(visited)>=max_pages:
            raise StoryFetchError('Sayfa sınırına ulaşıldı; eksik içerik gönderilmedi.')
    if not texts:
        raise StoryFetchError('Metin bulunamadı.')
    return '\n\n'.join('[Kaynak sayfa '+str(i)+']\n'+v for i,v in enumerate(texts,1)),list(visited)

def parse_user_input(text):
    urls=[]
    rest=[]
    for line in text.splitlines():
        value=line.strip()
        if re.fullmatch(r'https?://\S+',value,flags=re.I):
            urls.append(value)
        elif value:
            rest.append(value)
    if urls and rest:
        raise StoryFetchError('Link ve metni aynı istekte karıştırmayın.')
    return ('links',urls) if urls else ('text',text.strip())
