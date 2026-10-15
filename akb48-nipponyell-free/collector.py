import datetime
import json
import re
import time
import unicodedata
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
MAX_PAGES_PER_SOURCE = 35
MAX_DEPTH = 2
PREFS = ('北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄').split()
EVENT_WORDS = ('イベント', '開催', '来場', '登場', '出演', '訪問', '巡回', 'キャラバン', '会場', 'ニッポンエール号')
LINK_WORDS = ('akb48', 'ニッポンエール', 'お知らせ', 'ニュース', 'イベント', 'トピックス', 'press', 'news', 'topic', 'event', 'もっと見る', '詳細', '一覧')


def load(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return default


def save(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def normalized_text(value):
    return unicodedata.normalize('NFKC', value or '').casefold()


def related(value):
    text = normalized_text(value)
    return 'akb48' in text and ('ニッポンエール' in text or 'にっぽんエール' in text or 'nippon yell' in text)


def normalize_url(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def classify(text):
    dates = list(dict.fromkeys(re.findall(r'(?:20\d{2}[年/.-])?\s*\d{1,2}\s*[月/.-]\s*\d{1,2}\s*日?', text)))[:8]
    prefectures = [p for p in PREFS if p in text]
    return {'dates_mentioned': dates, 'prefectures_mentioned': prefectures, 'members': [], 'status': '要確認'}


def extract_page(soup):
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    main = soup.find('article') or soup.find('main') or soup.body or soup
    return title, main.get_text(' ', strip=True)[:18000]


def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds')
    old = {x['url']: x for x in load('candidates.json', []) if isinstance(x, dict) and x.get('url')}
    found, errors = {}, []
    stats = {'pages_checked': 0, 'posts_checked': 0, 'sources_enabled': 0}
    session = requests.Session()
    session.headers.update(HEADERS)
    robots_cache = {}

    def allowed(url):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https':
            return False
        host = parts.netloc.lower()
        if host not in robots_cache:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f'https://{host}/robots.txt')
            try:
                response = session.get(f'https://{host}/robots.txt', timeout=12)
                if response.status_code == 404:
                    robots_cache[host] = None
                elif response.ok:
                    parser.parse(response.text.splitlines())
                    robots_cache[host] = parser
                else:
                    robots_cache[host] = False
            except requests.RequestException:
                robots_cache[host] = False
        rule = robots_cache[host]
        return rule is None or (rule is not False and rule.can_fetch(HEADERS['User-Agent'], url))

    def record(url, title, source, kind, text):
        prior = old.get(url, {})
        found[url] = {**prior, 'title': title[:220] or url, 'url': url, 'source_type': kind,
                      'source': source, 'first_seen': prior.get('first_seen', now),
                      'last_seen': now, **classify(text)}

    for source in SOURCES:
        if not source.get('enabled', True):
            continue
        stats['sources_enabled'] += 1
        start = source.get('url', '')
        kind = source.get('type', 'website')
        print(f'[SOURCE] {kind}: {start}', flush=True)

        if kind == 'website':
            if not start.startswith('https://'):
                errors.append({'source': start, 'error': 'HTTPS URL required'})
                continue
            base_host = urllib.parse.urlsplit(start).netloc.lower()
            queue = deque([(normalize_url(start), 0)])
            visited = set()
            while queue and len(visited) < MAX_PAGES_PER_SOURCE:
                url, depth = queue.popleft()
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
                    # Decode HTML from bytes. BeautifulSoup reads HTML charset metadata;
                    # apparent_encoding is used only if the page does not declare one.
                    soup = BeautifulSoup(response.content, 'html.parser', from_encoding='utf-8')
                    title, text = extract_page(soup)
                    stats['pages_checked'] += 1
                    combined = title + ' ' + text
                    has_akb = 'akb48' in normalized_text(combined)
                    has_yell = 'ニッポンエール' in normalized_text(combined) or 'にっぽんエール' in normalized_text(combined)
                    print(f'[PAGE] {url} | TITLE: {title[:95]} | AKB48: {has_akb} | ニッポンエール: {has_yell}', flush=True)
                    # This is only a candidate, not a confirmed member appearance.
                    if related(combined) and any(word in normalized_text(combined) for word in EVENT_WORDS):
                        record(url, title, start, 'JA公式サイト', text)
                        print(f'[CANDIDATE] {url}', flush=True)
                    if depth < MAX_DEPTH:
                        for anchor in soup.find_all('a', href=True):
                            href = normalize_url(urllib.parse.urljoin(url, anchor['href']))
                            parsed = urllib.parse.urlsplit(href)
                            if parsed.scheme != 'https' or parsed.netloc.lower() != base_host:
                                continue
                            if re.search(r'\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?)(?:$|\?)', parsed.path, re.I):
                                continue
                            label = anchor.get_text(' ', strip=True)
                            candidate_label = normalized_text(label + ' ' + href)
                            if any(word in candidate_label for word in LINK_WORDS) and href not in visited:
                                queue.append((href, depth + 1))
                    time.sleep(0.3)
                except requests.RequestException as exc:
                    errors.append({'source': url, 'error': str(exc)[:240]})

        elif kind == 'bluesky':
            handles = {h.lstrip('@').lower() for h in source.get('allowed_handles', []) if h}
            if not handles:
                errors.append({'source': start, 'error': 'Bluesky skipped: allowed_handles is empty'})
                continue
            try:
                response = session.get('https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts',
                                       params={'q': source.get('query', 'ニッポンエール AKB48'),
                                               'sort': 'latest', 'limit': 100}, timeout=18)
                response.raise_for_status()
                for post in response.json().get('posts', []):
                    stats['posts_checked'] += 1
                    handle = post.get('author', {}).get('handle', '').lower()
                    text = post.get('record', {}).get('text', '')
                    uri = post.get('uri', '')
                    if handle not in handles or not related(text) or not uri:
                        continue
                    link = f'https://bsky.app/profile/{handle}/post/{uri.rsplit("/", 1)[-1]}'
                    record(link, text[:220], handle, 'Bluesky公式アカウント（登録制）', text)
            except (requests.RequestException, ValueError) as exc:
                errors.append({'source': start, 'error': str(exc)[:240]})

    merged = {**old, **found}
    save('candidates.json', sorted(merged.values(), key=lambda x: x.get('first_seen', ''), reverse=True))
    history = load('history.json', [])
    for url, item in found.items():
        if url not in old:
            history.insert(0, {'at': now, 'type': '新規候補', 'title': item['title'], 'url': url})
    save('history.json', history[:500])
    new_count = sum(url not in old for url in found)
    save('status.json', {'last_run': now, 'sources_configured': len(SOURCES),
                         'new_candidates': new_count, 'candidates_total': len(merged),
                         **stats, 'errors': errors[:100]})
    print(f'Checked {stats["pages_checked"]} pages, {stats["posts_checked"]} posts; new {new_count}; errors {len(errors)}', flush=True)


if __name__ == '__main__':
    main()
