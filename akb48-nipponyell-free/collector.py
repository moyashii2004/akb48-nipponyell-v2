import datetime
import hashlib
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
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SOURCES = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
HEADERS = {"User-Agent": "AKB48-NipponYell-PersonalTracker/1.3 (public pages only)"}
MAX_PAGES_PER_SOURCE = 35
MAX_DEPTH = 2
PREFS = ("北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 "
         "新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 "
         "和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 "
         "大分 宮崎 鹿児島 沖縄").split()
TOPIC_WORDS = ("ニッポンエール", "にっぽんエール", "nippon yell", "ニッポンエール号")
EVENT_WORDS = ("イベント", "開催", "来場", "登場", "出演", "訪問", "巡回", "キャラバン", "会場", "お披露目", "出発式")
LINK_WORDS = ("akb48", "ニッポンエール", "お知らせ", "ニュース", "イベント", "トピックス", "press", "news", "topic", "event", "詳細", "一覧", "県本部", "記事")
LISTING_PATHS = ("/topics/index", "/press/release/index", "/article_type/prefnews")


def load(name, default):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return default


def save(name, value):
    (DATA / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(value):
    return unicodedata.normalize("NFKC", value or "").casefold()


def url_clean(value):
    p = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def is_listing(url, title):
    path = urllib.parse.urlsplit(url).path.lower()
    return (any(part in path for part in LISTING_PATHS)
            or path.endswith("/index.html")
            or norm(title).startswith(("お知らせ一覧", "ニュースリリース", "県本部だより")))


def extract_dates(text):
    matches = re.findall(r"(?:20\d{2}[年/.-])?\s*\d{1,2}\s*[月/.-]\s*\d{1,2}\s*日?", norm(text))
    return list(dict.fromkeys(x.strip() for x in matches if x.strip()))[:12]


def extract_page(soup):
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = BeautifulSoup(str(soup), "html.parser")
    for tag in body(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    main = body.find("article") or body.find("main") or body.body or body
    return title, main.get_text(" ", strip=True)[:24000]


def relevant(title, text):
    t = norm(title)
    body = norm(text)
    has_akb = "akb48" in t
    has_topic = any(word in t for word in TOPIC_WORDS)
    # 一般ニュースの誤検出を避け、タイトルにAKB48がある記事を基本とする。
    # 例外: ニッポンエール号の記事にAKB48出演の明記がある場合。
    return ((has_akb and (has_topic or any(word in body for word in TOPIC_WORDS)))
            or ("ニッポンエール号" in t and "akb48" in body))


def classify(title, text):
    t = norm(title)
    body = norm(text)
    event_in_title = any(word in t for word in EVENT_WORDS)
    # 「AKB48が出演」「AKB48のメンバーが登場」等、近接した表現だけを拾う。
    explicit = bool(re.search(r"akb48.{0,35}(?:出演|登場|来場|参加|訪問)", body)
                    or re.search(r"(?:出演|登場|来場|参加|訪問).{0,35}akb48", body))
    if event_in_title and explicit:
        category = "イベント出演の可能性・要確認"
    elif event_in_title:
        category = "イベント関連の可能性・要確認"
    else:
        category = "AKB48関連ニュース（出演未確認）"
    return {
        "category": category,
        "dates_mentioned": extract_dates(text),
        "prefectures_mentioned": [p for p in PREFS if p in text],
        "members": [],
        "status": "要確認",
        "appearance_confirmed": False,
        "event_date": None,
        "venue": None,
        "evidence_excerpt": "",
    }


def main():
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")
    previous = load("candidates.json", [])
    old = {x["url"]: x for x in previous if isinstance(x, dict) and x.get("url")}
    found = {}
    errors = []
    stats = {"pages_checked": 0, "posts_checked": 0, "sources_enabled": 0,
             "listing_pages_skipped": 0, "sources_checked": 0}
    session = requests.Session()
    session.headers.update(HEADERS)
    robots_cache = {}

    def allowed(url):
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https":
            return False
        host = p.netloc.lower()
        if host not in robots_cache:
            parser = urllib.robotparser.RobotFileParser()
            try:
                res = session.get(f"https://{host}/robots.txt", timeout=12)
                if res.status_code == 404:
                    robots_cache[host] = None
                elif res.ok:
                    parser.parse(res.text.splitlines())
                    robots_cache[host] = parser
                else:
                    robots_cache[host] = False
            except requests.RequestException:
                robots_cache[host] = False
        rule = robots_cache[host]
        return rule is None or (rule is not False and rule.can_fetch(HEADERS["User-Agent"], url))

    def record(url, title, source, text, source_type="JA公式サイト"):
        prior = old.get(url, {})
        item = {"title": title[:220] or url, "url": url, "source_type": source_type,
                "source": source, "first_seen": prior.get("first_seen", now),
                "last_seen": now, **classify(title, text)}
        # 人が検証した確定情報を自動処理で消さない。
        if prior.get("appearance_confirmed") is True:
            for key in ("members", "status", "appearance_confirmed", "event_date", "venue", "evidence_excerpt"):
                item[key] = prior.get(key, item[key])
        found[url] = item

    for source in SOURCES:
        if not source.get("enabled", True):
            continue
        stats["sources_enabled"] += 1
        start = source.get("url", "")
        kind = source.get("type", "website")
        print(f"[SOURCE] {kind}: {start}", flush=True)
        if kind == "website":
            if not start.startswith("https://"):
                errors.append({"source": start, "error": "HTTPS URL required"})
                continue
            host = urllib.parse.urlsplit(start).netloc.lower()
            queue = deque([(url_clean(start), 0)])
            visited = set()
            while queue and len(visited) < MAX_PAGES_PER_SOURCE:
                url, depth = queue.popleft()
                if url in visited:
                    continue
                visited.add(url)
                if not allowed(url):
                    errors.append({"source": url, "error": "robots.txt disallows or unavailable"})
                    print(f"[SKIP] robots.txt: {url}", flush=True)
                    continue
                try:
                    res = session.get(url, timeout=18)
                    res.raise_for_status()
                    final_url = url_clean(res.url)
                    if urllib.parse.urlsplit(final_url).netloc.lower() != host or not final_url.startswith("https://"):
                        continue
                    if "html" not in res.headers.get("Content-Type", "").lower():
                        continue
                    soup = BeautifulSoup(res.content, "html.parser", from_encoding="utf-8")
                    title, text = extract_page(soup)
                    stats["pages_checked"] += 1
                    listing = is_listing(final_url, title)
                    if listing:
                        stats["listing_pages_skipped"] += 1
                    elif relevant(title, text):
                        record(final_url, title, start, text)
                        print(f"[CANDIDATE] {title[:90]} | {final_url}", flush=True)
                    if depth < MAX_DEPTH:
                        for anchor in soup.find_all("a", href=True):
                            link = url_clean(urllib.parse.urljoin(final_url, anchor["href"]))
                            parsed = urllib.parse.urlsplit(link)
                            if parsed.scheme != "https" or parsed.netloc.lower() != host:
                                continue
                            if re.search(r"\.(?:pdf|jpe?g|png|gif|zip|docx?|xlsx?)$", parsed.path, re.I):
                                continue
                            label = norm(anchor.get_text(" ", strip=True) + " " + link)
                            if any(word in label for word in LINK_WORDS) and link not in visited:
                                queue.append((link, depth + 1))
                    time.sleep(0.3)
                except requests.RequestException as exc:
                    errors.append({"source": url, "error": str(exc)[:240]})
            stats["sources_checked"] += 1
        elif kind == "bluesky":
            handles = {h.lstrip("@").lower() for h in source.get("allowed_handles", []) if h}
            if not handles:
                errors.append({"source": start, "error": "Bluesky skipped: allowed_handles is empty"})
                continue
            try:
                res = session.get("https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                                  params={"q": source.get("query", "ニッポンエール AKB48"),
                                          "sort": "latest", "limit": 100}, timeout=18)
                res.raise_for_status()
                for post in res.json().get("posts", []):
                    stats["posts_checked"] += 1
                    handle = post.get("author", {}).get("handle", "").lower()
                    content = post.get("record", {}).get("text", "")
                    uri = post.get("uri", "")
                    if handle not in handles or not uri or not relevant(content, content):
                        continue
                    link = f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}"
                    record(link, content[:220], handle, content, "Bluesky公式アカウント（登録制）")
            except (requests.RequestException, ValueError) as exc:
                errors.append({"source": start, "error": str(exc)[:240]})

    # 過去の手動確認データは保護。その他は今回の収集結果を正とする。
    confirmed = {u: x for u, x in old.items() if x.get("appearance_confirmed") is True}
    merged = {**confirmed, **found}
    save("candidates.json", sorted(merged.values(), key=lambda x: x.get("first_seen", ""), reverse=True))
    history = load("history.json", [])
    for url, item in found.items():
        if url not in old:
            history.insert(0, {"at": now, "type": "新規候補", "title": item["title"], "url": url})
        elif any(old[url].get(k) != item.get(k) for k in ("title", "category", "appearance_confirmed", "event_date", "venue", "members")):
            history.insert(0, {"at": now, "type": "候補更新", "title": item["title"], "url": url})
    save("history.json", history[:500])
    save("status.json", {"last_run": now, "sources_configured": len(SOURCES),
                         "new_candidates": sum(url not in old for url in found),
                         "candidates_total": len(merged), **stats, "errors": errors[:100]})
    print(f"Checked {stats['pages_checked']} pages; candidates {len(merged)}; errors {len(errors)}", flush=True)


if __name__ == "__main__":
    main()
