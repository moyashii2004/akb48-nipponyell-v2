import datetime
import json
import re
import time
import urllib.parse
import urllib.robotparser
from collections import deque
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
SOURCES = json.loads((ROOT / 'sources.json').read_text(encoding='utf-8'))
HEADERS = {'User-Agent': 'AKB48-NipponYell-PersonalTracker/1.2 (public pages only)'}
PREFS = ('北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄').split()
EVENT_WORDS = ('イベント', '開催', '来場', '登場', '出演', '訪問', '巡回', 'キャラバン', '会場', 'ニッポンエール号')
LINK_HINTS = ('AKB48', 'ニッポンエール', 'お知らせ', 'ニュース', 'イベント', 'トピックス', 'press', 'news', 'topic', 'event', '詳細', 'もっと見る', '一覧')
MAX_PAGES_PER_SOURCE = 35
MAX_DEPTH = 2


def load(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def normalize(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path or '/', p.query, ''))


def relevant(text):
    t = text.casefold()
    return ('akb48' in t or 'ａｋｂ４８' in t) and ('ニッポンエール' in t or 'にっぽんエール' in t or 'nippon yell' in t)


def classify(text):
    dates = list(dict.fromkeys(re.findall(r'(?:20\d{2}[年/.-])?\s*\d{1,2}\s*[月/.-]\s*\d{1,2}\s*日?', text)))[:8]
    return {'dates_mentioned': dates, 'prefectures_mentioned': [p for p in PREFS if p in text], 'members': [], 'status': '要確認'}


def page_text(soup):
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    main = soup.find('main') or soup.find('article') or soup.body or soup
    return main.get_text(' ', strip=True)[:18000]


def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds')
    old = {x['url']: x for x in load('candidates.json', []) if isinstance(x, dict) and x.get('url')}
    found, errors = {}, []
    pages_checked = posts_checked = enabled_count = 0
    robots_cache = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    def allowed(url):
        p = urllib.parse.urlsplit(url)
        if p.scheme != 'https':
            return False
        host = p.netloc.lower()
        if host not in robots_cache:
            try:
                response = session.get(f'https://{host}/robots.txt', timeout=12)
                if response.status_code == 404:
                    robots_cache[host] = None
                elif response.ok:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(response.text.splitlines())
                    robots_cache[host] = rp
                else:
                    robots_cache[host] = False
            except requests.RequestException:
                robots_cache[host] = False
        rule = robots_cache[host]
        return rule is None or (rule is not False and rule.can_fetch(HEADERS['User-Agent'], url))

    def record(url, title, source, kind, body):
        prior = old.get(url, {})
        found[url] = {**prior, 'title': title[:220] or url, 'url': url, 'source_type': kind, 'source': source, 'first_seen': prior.get('first_seen', now), 'last_seen': now, **classify(body)}

    for source in SOURCES:
        if not source.get('enabled', True):
            continue
        enabled_count += 1
        start_url = source.get('url', '')
        kind = source.get('type', 'website')
        print(f'[SOURCE] {kind}: {start_url}', flush=True)

        if kind == 'website':
            if not start_url.startswith('https://'):
                errors.append({'source': start_url, 'error': 'HTTPS URL required'})
                continue
            base_host = urllib.parse.urlsplit(start_url).netloc.lower()
            queue = deque([(normalize(start_url), 0, '')])
            visited = set()
            while queue and len(visited) < MAX_PAGES_PER_SOURCE:
                url, depth, link_title = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                if not allowed(url):
                    errors.append({'source': url, 'error': 'robots.txt disallows or unavailable'})
                    print(f'[SKIP] robots.txt: {url}', flush=True)
                    continue
                try:
                    response = session.get(url, timeout=18)
                    response.raise_for_status()
                    if 'html' not in response.headers.get('Content-Type', '').lower():
                        continue
                    soup = BeautifulSoup(response.text, 'html.parser')
                    title = (soup.title.get_text(' ', strip=True) if soup.title else link_title)[:220]
                    body = page_text(soup)
                    combined = title + ' ' + body
                    pages_checked += 1
                    has_akb = 'akb48' in combined.casefold()
                    has_yell = 'ニッポンエール' in combined or 'にっぽんエール' in combined or 'nippon yell' in combined.casefold()
                    print(f'[PAGE] {response.url} | TITLE: {title[:100]} | AKB48: {has_akb} | ニッポンエール: {has_yell}', flush=True)
                    if relevant(combined) and any(w in combined for w in EVENT_WORDS):
                        record(url, title, start_url, 'JA公式サイト', body)
                        print(f'[CANDIDATE] {url}', flush=True)
                    if depth < MAX_DEPTH:
                        for a in soup.find_all('a', href=True):
                            href = normalize(urllib.parse.urljoin(response.url, a['href']))
                            parsed = urllib.parse.urlsplit(href)
                            if parsed.scheme != 'https' or parsed.netloc.lower() != base_host:
                                continue
                            if re.search(r'\.(pdf|jpg|jpeg|png|gif|zip|docx?|xlsx?)(?:$|\?)', parsed.path, re.I):
                                continue
                            label = a.get_text(' ', strip=True)[:220]
                            if any(w.casefold() in (label + ' ' + href).casefold() for w in LINK_HINTS) and href not in visited:
                                queue.append((href, depth + 1, label))
                    time.sleep(0.3)
                except requests.RequestException as exc:
                    errors.append({'source': url, 'error': str(exc)[:240]})
                    print(f'[ERROR] {url}: {exc}', flush=True)

        elif kind == 'bluesky':
            handles = [h.lstrip('@').lower() for h in source.get('allowed_handles', []) if h]
            if not handles:
                errors.append({'source': start_url, 'error': 'Bluesky skipped: allowed_handles is empty'})
                continue
            try:
                response = session.get('https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts', params={'q': source.get('query', 'ニッポンエール AKB48'), 'sort': 'latest', 'limit': 100}, timeout=18)
                response.raise_for_status()
                for post in response.json().get('posts', []):
                    posts_checked += 1
                    handle = post.get('author', {}).get('handle', '').lower()
                    body = post.get('record', {}).get('text', '')
                    uri = post.get('uri', '')
                    if handle not in handles or not relevant(body) or not uri:
                        continue
                    link = f'https://bsky.app/profile/{handle}/post/{uri.rsplit("/", 1)[-1]}'
                    record(link, body[:220], handle, 'Bluesky公式アカウント（登録制）', body)
            except (requests.RequestException, ValueError) as exc:
                errors.append({'source': start_url, 'error': str(exc)[:240]})

    merged = {**old, **found}
    save('candidates.json', sorted(merged.values(), key=lambda x: x.get('first_seen', ''), reverse=True))
    history = load('history.json', [])
    for url, item in found.items():
        if url not in old:
            history.insert(0, {'at': now, 'type': '新規候補', 'title': item['title'], 'url': url})
    save('history.json', history[:500])
    new_count = sum(url not in old for url in found)
    save('status.json', {'last_run': now, 'sources_configured': len(SOURCES), 'new_candidates': new_count, 'candidates_total': len(merged), 'pages_checked': pages_checked, 'posts_checked': posts_checked, 'sources_enabled': enabled_count, 'errors': errors[:100]})
    print(f'Checked {pages_checked} pages, {posts_checked} posts; new {new_count}; errors {len(errors)}', flush=True)


if __name__ == '__main__':
    main()
