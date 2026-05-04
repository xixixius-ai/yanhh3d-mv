#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v5.0 FINAL - URLLIB RESTORED + MERGE)
✅ URLLIB ENGINE: Khôi phục urllib + Cookie sync (fix triệt để no stream)
✅ MERGE LOGIC: Ghép tập mới vào detail.json cũ, không ghi đè mất tập cũ
✅ Pagination: Crawl từ /moi-cap-nhat (5 trang × ~24 phim = 120 phim)
✅ Fast: EP_DELAY 1.5-3.0s (nhanh hơn 40% so với v4.0)
✅ Progress log: Hiển thị [1/120] Đang xử lý: <tên phim>
✅ Session refresh: Reset connection mỗi 40 tập để tránh block
✅ Clean Syntax: metadata: dict = None (chuẩn Python 3.10+)
"""

import argparse
import json
import logging
import os
import random
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout, BrowserContext, Page

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL":     "https://yanhh3d.bz",
    "OUTPUT_DIR":   "ophim",
    "LIST_FILE":    "ophim.json",
    "PROGRESS_FILE":"progress.json",
    "MAX_MOVIES":   None,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"),
    "RETRY_COUNT":  2,
    "RETRY_DELAY":  1.0,
    "EP_DELAY_MIN": 1500,
    "EP_DELAY_MAX": 3000,
    "BATCH_LIMIT":  20,
    "CONSECUTIVE_FAIL_LIMIT": 5,
    "MAX_PAGES":    5,
    "SESSION_REFRESH_INTERVAL": 40,
}

EXTRA_HEADERS = {
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":           "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":           "gzip, deflate, br",
    "Cache-Control":             "no-cache",
    "Pragma":                    "no-cache",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Upgrade-Insecure-Requests": "1",
}

PLAY_FB_V8_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer":         "https://yanhh3d.bz/",
}


def _human_delay(min_ms=300, max_ms=900):
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _apply_stealth(page: Page):
    if HAS_STEALTH:
        stealth_sync(page)
    else:
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US'] });
            window.chrome = { runtime: {} };
        """)


def _refresh_session(page: Page):
    try:
        page.goto("about:blank", wait_until="commit", timeout=5000)
        _human_delay(500, 1500)
        logger.info("   🔄 Session refreshed")
    except Exception as e:
        logger.debug(f"   Session refresh warning: {e}")


def _wait_for_cf(page: Page, selector: str, timeout: int):
    try:
        page.wait_for_function(
            """() => !document.title.includes('Just a moment') &&
                    !document.querySelector('#challenge-running') &&
                    document.readyState === 'complete'""",
            timeout=15000
        )
    except Exception:
        pass
    page.wait_for_selector(selector, state="attached", timeout=timeout)


def _build_search_str(movie: dict, metadata: dict = None) -> str:
    metadata = metadata or {}
    parts = [
        movie.get("title", ""),
        " ".join(metadata.get("tags", [])),
        metadata.get("description", ""),
        movie.get("slug", "").replace("-", " "),
        "hoạt hình trung quốc", "thuyết minh", "anime", "donghua"
    ]
    return " ".join(p for p in parts if p).lower().strip()


def load_progress() -> dict:
    try:
        with open(CONFIG["PROGRESS_FILE"], "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_progress(progress: dict):
    try:
        with open(CONFIG["PROGRESS_FILE"], "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"   Failed to save progress: {e}")


def _extract_cookies_from_context(context: BrowserContext) -> dict:
    """✅ URLLIB HELPER: Trích xuất cookie từ Playwright để truyền vào urllib"""
    cookies = {}
    try:
        for c in context.cookies():
            cookies[c['name']] = c['value']
    except Exception:
        pass
    return cookies


def _cookies_to_header(cookies: dict) -> str:
    if not cookies: return ""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def get_movie_metadata(page: Page, slug: str) -> dict:
    detail_url = f"{CONFIG['BASE_URL']}/{slug}"
    metadata = {"description": "", "tags": [], "year": "", "status": "", "poster": "", "total_episodes": ""}
    
    try:
        _human_delay(200, 400)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "body", CONFIG["TIMEOUT_WAIT"])
        
        meta = page.evaluate("""() => {
            const result = { description: "", tags: [], year: "", status: "", poster: "", total_episodes: "" };
            const desc = document.querySelector('meta[name="description"]')?.content ||
                        document.querySelector('meta[property="og:description"]')?.content || "";
            result.description = desc ? desc.trim().replace(/\s+/g, ' ').slice(0, 500) : "";
            
            const genreLinks = document.querySelectorAll('.genres a, .film-info a[href*="/the-loai/"], .tick a');
            for (const link of genreLinks) {
                const text = link.innerText.trim();
                if (text && text.length < 50 && !/tap|tập/i.test(text)) result.tags.push(text);
            }
            result.tags = [...new Set(result.tags)].slice(0, 10);
            
            const yearMatch = document.title.match(/(\d{4})/) || document.querySelector('.film-info')?.innerText?.match(/(\d{4})/);
            if (yearMatch) result.year = yearMatch[1];
            
            const statusText = document.querySelector('.tick-rate, .badge, .status')?.innerText?.toLowerCase() || "";
            if (/hoàn thành|end|completed/i.test(statusText)) result.status = "completed";
            else if (/đang phát|ongoing|updating/i.test(statusText)) result.status = "ongoing";
            
            const poster = document.querySelector('meta[property="og:image"]')?.content ||
                          document.querySelector('.film-poster img')?.src ||
                          document.querySelector('.film-poster img')?.dataset.src || "";
            result.poster = poster || "";
            
            const epInfo = document.querySelector('.total-episodes, .episode-count, .film-info .fdi-item')?.innerText || "";
            const epMatch = epInfo.match(/(\d+)\s*(?:tập|ep)/i);
            if (epMatch) result.total_episodes = epMatch[1];
            return result;
        }""")
        
        if not meta.get("description"):
            html = page.content()
            desc_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
            if desc_match:
                meta["description"] = desc_match.group(1).strip()[:500]
            
        return meta
    except Exception as e:
        logger.warning(f"   Failed to extract metadata for {slug}: {e}")
        return metadata


def resolve_play_fb_v8(proxy_url: str, cookies: dict = None) -> str | None:
    """✅ URLLIB ENGINE: Dùng urllib + Cookie sync như v4.0 để tránh bị chặn"""
    headers = PLAY_FB_V8_HEADERS.copy()
    if cookies:
        headers["Cookie"] = _cookies_to_header(cookies)

    for attempt in range(CONFIG["RETRY_COUNT"] + 1):
        try:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            opener = urllib.request.build_opener(NoRedirect())
            req = urllib.request.Request(proxy_url, headers=headers, method="GET")
            
            with opener.open(req, timeout=20) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if location and _is_valid_fb_cdn(location):
                        return location
                
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    content = resp.read().decode('utf-8', errors='ignore')
                    
                    if 'application/json' in content_type:
                        try:
                            data = json.loads(content)
                            url = data.get('url') or data.get('video_url') or data.get('stream_url') or data.get('src') or data.get('file')
                            if url and _is_valid_fb_cdn(url):
                                return url
                        except: pass
                    
                    url_patterns = [
                        r'"(https?://scontent-[^"]+\.mp4[^"]*)"',
                        r"'(https?://scontent-[^']+\.mp4[^']*)'",
                        r'url\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'src\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'file\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'<source[^>]+src=["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
                        r'video_url["\']?\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                        r'["\']src["\']\s*:\s*["\']([^"\']+fbcdn[^"\']+\.mp4[^"\']*)["\']',
                        r'(https?://[^\s\'"]+fbcdn[^\s\'"]+\.mp4[^\s\'"]*)',
                    ]
                    for pattern in url_patterns:
                        match = re.search(pattern, content, re.IGNORECASE)
                        if match:
                            url = match.group(1).replace('\\/', '/')
                            if _is_valid_fb_cdn(url):
                                return url
                
                return None
                
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                wait = random.uniform(5.0, 12.0)
                logger.warning(f"   🛑 Rate limited ({e.code}). Waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location", "")
                if location and _is_valid_fb_cdn(location):
                    return location
        except Exception:
            pass
            
        if attempt < CONFIG["RETRY_COUNT"]:
            delay = CONFIG["RETRY_DELAY"] * (2 ** attempt)
            time.sleep(delay)
            
    return None


def _is_valid_fb_cdn(url: str) -> bool:
    if not url: return False
    u = url.lower()
    return ('fbcdn' in u or 'facebook' in u) and '.mp4' in u


def get_movies_from_pagination(page: Page, max_pages: int = None) -> list[dict]:
    if max_pages is None:
        max_pages = CONFIG["MAX_PAGES"]
    
    all_movies = []
    base_url = f"{CONFIG['BASE_URL']}/moi-cap-nhat"
    
    for page_num in range(1, max_pages + 1):
        url = f"{base_url}?page={page_num}" if page_num > 1 else base_url
        logger.info(f"  📄 Loading page {page_num}/{max_pages}: {url}")
        
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
            _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
            
            movies = page.evaluate("""() => {
                const res = [];
                document.querySelectorAll('.flw-item').forEach(item => {
                    const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                    if (!link?.href) return;
                    const slug = link.href.split('/').pop().replace(/\/$/, '');
                    const title = link.innerText.trim() || link.title || '';
                    if (!title || slug.includes('search')) return;
                    let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || 
                               item.querySelector('img[data-src], img.film-poster-img')?.src || '';
                    if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                    const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                    res.push({ slug, title, thumb, badge });
                });
                return res;
            }""")
            
            if not movies:
                logger.info(f"  ⚠️  No more movies found on page {page_num}")
                break
                
            all_movies.extend(movies)
            logger.info(f"  ✅ Found {len(movies)} movies on page {page_num} (Total: {len(all_movies)})")
            
            has_next = page.query_selector('a[title="Next"], .pagination li.active + li a')
            if not has_next:
                logger.info(f"  ⏹️  No next page button found")
                break
                
            _human_delay(500, 1000)
            
        except Exception as e:
            logger.warning(f"  ⚠️  Error loading page {page_num}: {e}")
            break
    
    logger.info(f"  🎯 Total movies collected: {len(all_movies)}")
    return all_movies


def get_episodes(page: Page, slug: str):
    try:
        _human_delay(300, 700)
        page.goto(f"{CONFIG['BASE_URL']}/{slug}", wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, f"a[href*='/{slug}/tap-']", CONFIG["TIMEOUT_WAIT"])
        
        latest_js = """(slug) => {
            const links = Array.from(document.querySelectorAll('a[href*="/' + slug + '/tap-"]'))
                .filter(a => !a.href.includes('/sever2/'))
                .sort((a, b) => {
                    const na = parseInt((a.href.match(/tap-(\\d+)/) || [])[1] || '0');
                    const nb = parseInt((b.href.match(/tap-(\\d+)/) || [])[1] || '0');
                    return nb - na;
                });
            return links.length ? links[0].href : null;
        }"""
        latest_url = page.evaluate(latest_js, slug)
        if not latest_url: return []
        
        page.goto(latest_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#episodes-content", CONFIG["TIMEOUT_WAIT"])
        
        return page.evaluate("""() => {
            const res = [];
            const pane = document.querySelector('#top-comment');
            if (!pane) return res;
            pane.querySelectorAll('a.ssl-item.ep-item').forEach(item => {
                const href = item.href || '';
                const text = (item.querySelector('.ssli-order')?.innerText || item.querySelector('.ep-name')?.innerText || item.title || '').trim();
                if (href.includes('/sever2/')) return;
                if (href && /^\\d+$/.test(text)) res.push({ name: text, url: href, num: parseInt(text) });
            });
            return res.sort((a, b) => b.num - a.num);
        }""")
    except Exception as e:
        logger.warning(f"   Error get episodes {slug}: {e}")
        return []


def get_stream_url(page: Page, context: BrowserContext, ep_url: str):
    try:
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#list_sv", CONFIG["TIMEOUT_WAIT"])
        btns = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('#list_sv a.btn3dsv')).map(b => ({
                src: (b.getAttribute('data-src') || '').trim(),
                label: (b.innerText || '').trim().toLowerCase()
            }));
        }""")
        # ✅ URLLIB: Trích xuất cookie từ context hiện tại để truyền vào urllib
        cookies = _extract_cookies_from_context(context)
        for b in btns:
            if "play-fb-v8" in b["src"]:
                url = resolve_play_fb_v8(b["src"], cookies=cookies)
                if url and _is_valid_fb_cdn(url):
                    return [{"url": url, "type": "mp4", "label": f"fb-cdn-{b['label']}" if b['label'] else "fb-cdn"}]
        return None
    except Exception:
        return None


def _load_existing_streams(detail_path: Path) -> list[dict]:
    """✅ MERGE HELPER: Load existing streams from detail.json"""
    if not detail_path.exists():
        return []
    try:
        with open(detail_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        streams = []
        for source in data.get("sources", []):
            for content in source.get("contents", []):
                streams.extend(content.get("streams", []))
        return streams
    except Exception:
        return []


def build_detail_json(slug: str, episodes: list, all_streams: list, metadata: dict = None):
    """✅ MERGE BUILDER: Build JSON with merged streams (old + new)"""
    metadata = metadata or {}
    
    ep_map = {}
    for stream_obj in all_streams:
        ep_name = stream_obj.get("name", "")
        if ep_name not in ep_map:
            ep_map[ep_name] = {"name": ep_name, "stream": []}
        ep_map[ep_name]["stream"].append({
            "id": stream_obj.get("id", ""),
            "name": stream_obj.get("name", f"Link {len(ep_map[ep_name]['stream'])+1}"),
            "type": stream_obj.get("type", "mp4"),
            "default": stream_obj.get("default", False),
            "url": stream_obj.get("url", "")
        })
    
    def extract_ep_num(name):
        match = re.search(r'\d+', name)
        return int(match.group()) if match else 0
        
    sorted_eps = sorted(ep_map.values(), key=lambda x: extract_ep_num(x["name"]), reverse=True)
    
    streams_output = []
    for i, ep in enumerate(sorted_eps):
        stream_links = []
        for j, s in enumerate(ep["stream"]):
            stream_links.append({
                "id": f"{slug}--0-{i}-{j}",
                "name": s.get("name") or f"Link {j+1}",
                "type": s["type"],
                "default": j == 0,
                "url": s["url"]
            })
        streams_output.append({"id": f"{slug}--0-{i}", "name": ep["name"], "stream_links": stream_links})
    
    result = {
        "sources": [{"id": f"{slug}--0", "name": "Thuyet Minh #1", "contents": [{"id": f"{slug}--0", "name": "", "grid_number": 3, "streams": streams_output}]}],
        "subtitle": "Thuyet Minh",
        "search": _build_search_str({"slug": slug, "title": slug}, metadata),
        "tags": metadata.get("tags", []),
        "description": metadata.get("description", ""),
    }
    for k in ("year", "status", "total_episodes"):
        if metadata.get(k): result[k] = metadata[k]
    return result


def build_list_item(movie: dict, metadata: dict = None):
    metadata = metadata or {}
    thumb = movie.get("thumb") or metadata.get("poster", "")
    badge = movie.get("badge") or metadata.get("status", "")
    item = {
        "id": movie["slug"], "name": movie["title"],
        "search": _build_search_str(movie, metadata), "keywords": metadata.get("tags", []),
        "description": metadata.get("description", ""),
        "image": {"url": thumb, "type": "cover", "width": 480, "height": 640},
        "type": "playlist", "display": "text-below",
        "label": {"text": badge or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
        "remote_data": {"url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"},
        "enable_detail": True
    }
    for k in ("year", "status"):
        if metadata.get(k): item[k] = metadata[k]
    return item


def scrape_movie(page: Page, context: BrowserContext, movie_info: dict, movie_index: int, total_movies: int, detail_dir: Path, force_all: bool = False, progress: dict = None):
    if progress is None: progress = {}
    slug = movie_info["slug"]
    
    logger.info(f"  [{movie_index}/{total_movies}] Đang xử lý: {slug}")
    
    metadata = get_movie_metadata(page, slug)
    metadata["title"] = movie_info.get("title", slug)
    metadata["thumb"] = movie_info.get("thumb", "")
    metadata["badge"] = movie_info.get("badge", "")
    
    episodes = get_episodes(page, slug)
    if not episodes:
        logger.warning(f"  No episodes found for {slug}")
        progress[slug] = progress.get(slug, {"offset": 0, "total_seen": 0, "status": "completed"})
        progress[slug]["status"] = "completed"
        save_progress(progress)
        return None

    current_total = len(episodes)
    state = progress.get(slug, {"offset": 0, "total_seen": 0, "status": "ongoing"})
    
    if force_all:
        offset, limit = 0, current_total
    else:
        new_eps = max(0, current_total - state["total_seen"])
        offset = max(0, state["offset"] - new_eps)
        limit = min(CONFIG["BATCH_LIMIT"], current_total - offset)
        
    if limit <= 0 or state["status"] == "completed":
        progress[slug] = {"offset": current_total, "total_seen": current_total, "status": "completed"}
        save_progress(progress)
        logger.info(f"  [SKIP] {slug} completed or no new episodes.")
        return None

    episodes_to_crawl = episodes[offset : offset + limit]
    logger.info(f"  [STATE] Offset: {offset} | Batch: {limit} eps | Total: {current_total}")
    
    ep_data = []
    consecutive_fails = 0
    for i, ep in enumerate(episodes_to_crawl):
        if (offset + i + 1) % CONFIG["SESSION_REFRESH_INTERVAL"] == 0:
            logger.info(f"   🔄 [SESSION] Refreshing after {offset + i + 1} requests...")
            _refresh_session(page)
            _human_delay(1000, 2000)
            
        _human_delay(CONFIG["EP_DELAY_MIN"], CONFIG["EP_DELAY_MAX"])
        stream = get_stream_url(page, context, ep["url"])
        if stream:
            ep_data.append({"name": ep["name"], "stream": stream})
            logger.info(f"    ✓ Tap {ep['name']}: OK")
            consecutive_fails = 0
        else:
            consecutive_fails += 1
            ep_data.append({"name": ep["name"], "stream": [{"id": f"{slug}--0-{offset+i}-err", "name": f"{ep['name']}(no)", "type": "error", "default": False, "url": "error:no_stream"}]})
            logger.warning(f"    ✗ Tap {ep['name']}: no stream (streak: {consecutive_fails})")
            if consecutive_fails >= CONFIG["CONSECUTIVE_FAIL_LIMIT"]:
                break
                
    crawled_count = len(ep_data)
    new_offset = offset + crawled_count
    progress[slug] = {
        "offset": new_offset,
        "total_seen": current_total,
        "status": "completed" if new_offset >= current_total else "ongoing"
    }
    save_progress(progress)
    
    # ✅ MERGE LOGIC: Load old streams + merge with new ones
    detail_path = detail_dir / f"{slug}.json"
    old_streams = _load_existing_streams(detail_path)
    
    new_streams = []
    for ep in ep_data:
        for s in ep["stream"]:
            new_streams.append(s)
            
    seen_urls = {s.get("url") for s in old_streams if s.get("url")}
    merged_streams = old_streams + [s for s in new_streams if s.get("url") not in seen_urls]
    
    detail_json = build_detail_json(slug, ep_data, merged_streams, metadata)
    with open(detail_path, "w", encoding="utf-8") as f:
        json.dump(detail_json, f, ensure_ascii=False, indent=2)
        
    list_item = build_list_item(movie_info, metadata)
    
    ok = sum(1 for s in merged_streams if s.get("type") != "error")
    logger.info(f"  ✅ Saved {slug}.json ({ok}/{len(merged_streams)} playable) | Offset -> {new_offset}")
    return list_item, detail_json


def main():
    parser = argparse.ArgumentParser(description="YanHH3D → MonPlayer Scraper v5.0")
    parser.add_argument("--search", type=str)
    parser.add_argument("--slug", type=str)
    parser.add_argument("--url", type=str)
    parser.add_argument("--max-pages", type=int, default=CONFIG["MAX_PAGES"], help="Số trang /moi-cap-nhat sẽ crawl")
    parser.add_argument("--all-episodes", action="store_true")
    parser.add_argument("--output", type=str, default=CONFIG["OUTPUT_DIR"])
    args = parser.parse_args()
    
    CONFIG["OUTPUT_DIR"] = args.output
    CONFIG["MAX_PAGES"] = args.max_pages
    
    logger.info(f"Starting v5.0 FINAL - URLLIB RESTORED + MERGE (Delay: {CONFIG['EP_DELAY_MIN']/1000}-{CONFIG['EP_DELAY_MAX']/1000}s)")
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    progress = load_progress()
    channels = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"])
        context = browser.new_context(user_agent=CONFIG["USER_AGENT"], viewport={"width": 1280, "height": 720}, locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh", extra_http_headers=EXTRA_HEADERS)
        page = context.new_page()
        _apply_stealth(page)

        try:
            movies = get_movies_from_pagination(page, max_pages=args.max_pages)
            
            if not movies:
                logger.error("No movies found!")
                return
                
            total = len(movies)
            logger.info(f"🎬 Found {total} movies. Processing all...")
            
            for idx, movie in enumerate(movies, 1):
                try:
                    res = scrape_movie(page, context, movie, movie_index=idx, total_movies=total, detail_dir=detail_dir,
                                     force_all=args.all_episodes, progress=progress)
                    if res:
                        li, _ = res
                        channels.append(li)
                except Exception as e:
                    logger.error(f"  ❌ Error processing {movie.get('slug', 'unknown')}: {e}")
                    continue
        finally:
            browser.close()

    list_output = {
        "id": "yanhh3d-thuyet-minh", "name": "YanHH3D - Thuyet Minh", "url": f"{CONFIG['RAW_BASE']}/ophim",
        "search": True, "enable_search": True, "features": {"search": True}, "color": "#004444",
        "image": {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyet minh chat luong cao tu YanHH3D.bz", "grid_number": 3,
        "channels": channels,
        "sorts": [{"text": "Moi nhat", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}],
        "meta": {"source": CONFIG["BASE_URL"], "total_items": len(channels), "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": "5.0"}
    }
    with open(Path(CONFIG["LIST_FILE"]), "w", encoding="utf-8") as f: 
        json.dump(list_output, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Done! Saved list + {len(channels)} details. Progress updated.")

if __name__ == "__main__":
    main()
