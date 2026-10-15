"""Official-source AKB48 x Nippon Yell news collector.

Candidates are *leads for review*, never confirmed appearances.
"""
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
USER_AGENT = 'AKB48-NipponYell-PersonalTracker/1.4 (public pages only)'
MAX_PAGES_PER_SOURCE = 35
MAX_DEPTH = 2
PREFS = ('北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 '
         '新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 '
         '和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 '
         '大分 宮崎 鹿児島 沖縄').split()
EVENT_WORDS = ('イベント', '開催', '来場', '登場', '出演', '訪問', '巡回', 'キャラバン', '会場', 'ニッポンエール号', 'お渡し会', 'トークショー')
LINK_WORDS = ('akb48', 'ニッポンエール', 'お知らせ', 'ニュース', 'イベント', 'トピックス',
              'press', 'news', 'topic', 'event', 'もっと見る', '詳細', '一覧')
DATE_PATTERN = re.compile(r'(?:(20\d{2})\s*[年/.-]\s*)?(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?')


def read_json(name, fallback):
    try:
        return json.loads((DATA / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def write_json(name, data):
    (DATA / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def norm(s):
    return unicodedata.normalize('NFKC', s or '').casefold()


def related(s):
    s = norm(s)
    return 'akb48' in s and ('ニッポンエール' in s or 'にっぽんエール' in s or 'nippon yell' in s)


def clean_url(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def is_listing(url):
    path = urllib.parse.urlsplit(url).path.lower().rstrip('/')
    return path.endswith('/index.html') or path.endswith('/newitems') or path in ('', '/')


def page_parts(soup):
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    for node in soup(['script', 'style', 'nav', 'footer', 'header']):
        node.decompose()
    body = soup.find('article') or soup.find('main') or soup.body or soup
    text = body.get_text(' ', strip=True)
    return title, text[:20000]


def relevant_article(title, text):
    """Require AKB48 in the article headline, not incidental body/sidebar text."""
    headline = norm(title.split(' | ')[0])
    return 'akb48' in headline and related(title + ' ' + text)


def classify(title, text):
    normalized = norm(title + ' ' + text)
    dates = []
    for match in DATE_PATTERN.finditer(normalized):
        y, m, d = match.groups()
        try:
            if y:
                date = datetime.date(int(y), int(m), int(d)).isoformat()
            else:
                datetime.date(2024, int(m), int(d))
                date = f'{int(m):02d}-{int(d):02d} (年不明)'
            if date not in dates:
                dates.append(date)
        except ValueError:
            pass
    # An event word anywhere on the page is not evidence of an AKB48 appearance.
    # Keep news separate until a human verifies the actual event and participants.
    return {
        'category': 'AKB48関連ニュース（出演未確認）',
        'dates_mentioned': dates[:12],
        'prefectures_mentioned': [p for p in PREFS if p in normalized],
        'members': [],
        'status': '要確認',
        'appearance_confirmed': False,
        'event_date': None,
        'venue': None,
    }


def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec='seconds')
    previous = {item['url']: item for item in read_json('candidates.json', [])
                if isinstance(item, dict) and item.get('url')}
    found = {}
    errors = []
    stats = {'pages_checked': 0, 'posts_checked': 0, 'sources_enabled': 0,
             'listing_pages_skipped': 0}
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT})
    robot_rules = {}

    def allowed(url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https':
            return False
        host = parsed.netloc.lower()
        if host not in robot_rules:
            try:
                res = session.get(f'https://{host}/robots.txt', timeout=12)
                if res.status_code == 404:
                    robot_rules[host] = None
                elif res.ok:
                    rp = urllib.robotparser.RobotFileParser()
                    rp.parse(res.text.splitlines())
                    robot_rules[host] = rp
                else:
                    robot_rules[host] = False
            except requests.RequestException:
                robot_rules[host] = False
        rule = robot_rules[host]
        return rule is None or (rule is not False and rule.can_fetch(USER_AGENT, url))

    def record(url, title, text, source, kind):
        prior = previous.get(url, {})
        data = classify(title, text)
        found[url] = {
            **prior,
            'title': title[:220] or url,
            'url': url,
            'source_type': kind,
            'source': source,
            'first_seen': prior.get('first_seen', now),
            'last_seen': now,
            **data,
        }
        # Preserve manually verified event information if present.
        if prior.get('appearance_confirmed') is True:
            for key in ('appearance_confirmed', 'event_date', 'venue', 'members', 'status'):
                found[url][key] = prior.get(key)

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
            host = urllib.parse.urlsplit(start).netloc.lower()
            queue = deque([(clean_url(start), 0)])
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
                    res = session.get(url, timeout=18)
                    res.raise_for_status()
                    if 'html' not in res.headers.get('Content-Type', '').lower():
                        continue
                    # Parse UTF-8 bytes, not requests' potentially incorrect text encoding.
                    soup = BeautifulSoup(res.content, 'html.parser', from_encoding='utf-8')
                    title, text = page_parts(soup)
                    stats['pages_checked'] += 1
                    content = title + ' ' + text
                    normalized = norm(content)
                    print(f'[PAGE] {url} | TITLE: {title[:95]} | AKB48: {"akb48" in normalized} | ニッポンエール: {"ニッポンエール" in normalized}', flush=True)
                    if is_listing(url):
                        stats['listing_pages_skipped'] += 1
                        print(f'[LISTING] not a candidate: {url}', flush=True)
                    elif relevant_article(title, text):
                        record(url, title, text, start, 'JA公式サイト')
                        print(f'[CANDIDATE] {url}', flush=True)
                    if depth < MAX_DEPTH:
                        for a in soup.find_all('a', href=True):
                            href = clean_url(urllib.parse.urljoin(url, a['href']))
                            parsed = urllib.parse.urlsplit(href)
                            if parsed.scheme != 'https' or parsed.netloc.lower() != host:
                                continue
                            if re.search(r'\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?)$', parsed.path, re.I):
                                continue
                            label = norm(a.get_text(' ', strip=True) + ' ' + href)
                            if any(word in label for word in LINK_WORDS) and href not in visited:
                                queue.append((href, depth + 1))
                    time.sleep(0.3)
                except requests.RequestException as exc:
                    errors.append({'source': url, 'error': str(exc)[:240]})

        elif kind == 'bluesky':
            handles = {h.lstrip('@').lower() for h in source.get('allowed_handles', []) if h}
            if not handles:
                print('[SKIP] Bluesky: no verified official handles', flush=True)
                continue
            try:
                res = session.get('https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts',
                                  params={'q': source.get('query', 'ニッポンエール AKB48'),
                                          'sort': 'latest', 'limit': 100}, timeout=18)
                res.raise_for_status()
                for post in res.json().get('posts', []):
                    stats['posts_checked'] += 1
                    handle = post.get('author', {}).get('handle', '').lower()
                    text = post.get('record', {}).get('text', '')
                    uri = post.get('uri', '')
                    if handle not in handles or not related(text) or not uri:
                        continue
                    post_url = f'https://bsky.app/profile/{handle}/post/{uri.rsplit("/", 1)[-1]}'
                    record(post_url, text[:220], text, handle, 'Bluesky公式アカウント（登録制）')
            except (requests.RequestException, ValueError) as exc:
                errors.append({'source': start, 'error': str(exc)[:240]})

    # Remove historical listing pages and unrelated headlines. Preserve manually verified items.
    merged = {
        url: item for url, item in previous.items()
        if not is_listing(url) and (
            item.get('appearance_confirmed') is True
            or relevant_article(item.get('title', ''), item.get('title', ''))
        )
    }
    merged.update(found)
    write_json('candidates.json', sorted(merged.values(), key=lambda x: x.get('first_seen', ''), reverse=True))
    history = read_json('history.json', [])
    for url, item in found.items():
        if url not in previous:
            history.insert(0, {'at': now, 'type': '新規候補', 'title': item['title'], 'url': url})
    write_json('history.json', history[:500])
    new_count = sum(url not in previous for url in found)
    write_json('status.json', {'last_run': now, 'sources_configured': len(SOURCES),
                               'new_candidates': new_count, 'candidates_total': len(merged),
                               **stats, 'errors': errors[:100]})
    print(f'Checked {stats["pages_checked"]} pages; new {new_count}; candidates {len(merged)}; errors {len(errors)}', flush=True)


if __name__ == '__main__':
    main()
