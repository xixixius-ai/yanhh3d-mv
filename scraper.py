#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v2.0)
Fixes:
  - Restore thumbnails & episode badges (passed correctly from list → detail)
  - Remove hardcoded 10-movie limit (respect --max-movies up to 50)
  - Extract poster & total_episodes from detail page
  - Fix all syntax/type hint errors from previous versions
  - Keep "no stream" fallback & anti-rate-limit delays
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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
    "MAX_MOVIES":   20,
    "MAX_EPISODES": 5,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"),
    "RETRY_COUNT":  2,
    "RETRY_DELAY":  1.0,
    "EP_DELAY_MIN": 1200,
    "EP_DELAY_MAX": 2200,
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


def _apply_stealth(page):
    if HAS_STEALTH:
        stealth_sync(page)
    else:
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US'] });
            window.chrome = { runtime: {} };
        """)


def _debug_page(page, label):
    try:
        title   = page.title()
        url_now = page.url
        html    = page.content()[:800].replace('\n', ' ')
        logger.info(f"   [DEBUG:{label}] title='{title}'")
        logger.info(f"   [DEBUG:{label}] url='{url_now}'")
        logger.info(f"   [DEBUG:{label}] html[:800]={html}")
    except Exception as e:
        logger.info(f"   [DEBUG:{label}] cannot read page: {e}")


def _wait_for_cf(page, selector, timeout):
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


# ── Search Index Builder ─────────────────────────────────────────────────────
def _build_search_str(movie: dict, metadata: dict = None) -> str:
    parts = [
        movie.get("title", ""),
        " ".join(metadata.get("tags", [])) if metadata else "",
        metadata.get("description", "") if metadata else "",
        movie.get("slug", "").replace("-", " "),
        "hoạt hình trung quốc", "thuyết minh", "anime", "donghua"
    ]
    return " ".join(p for p in parts if p).lower().strip()


# ── Extract Movie Metadata (Detail Page) ─────────────────────────────────────
def get_movie_metadata(page, slug: str) -> dict:
    detail_url = f"{CONFIG['BASE_URL']}/{slug}"
    metadata = {"description": "", "tags": [], "year": "", "status": "", "poster": "", "total_episodes": ""}
    
    try:
        _human_delay(200, 400)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "body", CONFIG["TIMEOUT_WAIT"])
        
        meta = page.evaluate("""() => {
            const result = { description: "", tags: [], year: "", status: "", poster: "", total_episodes: "" };
            
            // Description
            const desc = document.querySelector('meta[name="description"]')?.content ||
                        document.querySelector('meta[property="og:description"]')?.content || "";
            result.description = desc ? desc.trim().replace(/\s+/g, ' ').slice(0, 500) : "";
            
            // Tags/Genres
            const genreLinks = document.querySelectorAll('.genres a, .film-info a[href*="/the-loai/"], .tick a');
            for (const link of genreLinks) {
                const text = link.innerText.trim();
                if (text && text.length < 50 && !/tap|tập/i.test(text)) result.tags.push(text);
            }
            result.tags = [...new Set(result.tags)].slice(0, 10);
            
            // Year
            const yearMatch = document.title.match(/(\d{4})/) || document.querySelector('.film-info')?.innerText?.match(/(\d{4})/);
            if (yearMatch) result.year = yearMatch[1];
            
            // Status
            const statusText = document.querySelector('.tick-rate, .badge, .status')?.innerText?.toLowerCase() || "";
            if (/hoàn thành|end|completed/i.test(statusText)) result.status = "completed";
            else if (/đang phát|ongoing|updating/i.test(statusText)) result.status = "ongoing";
            
            // Poster (fallback if homepage thumb missing)
            const poster = document.querySelector('meta[property="og:image"]')?.content ||
                          document.querySelector('.film-poster img')?.src ||
                          document.querySelector('.film-poster img')?.dataset.src || "";
            result.poster = poster || "";
            
            // Total episodes hint
            const epInfo = document.querySelector('.total-episodes, .episode-count, .film-info .fdi-item')?.innerText || "";
            const epMatch = epInfo.match(/(\d+)\s*(?:tập|ep)/i);
            if (epMatch) result.total_episodes = epMatch[1];
            
            return result;
        }""")
        
        if not meta.get("description"):
            html = page.content()
            desc_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
            if desc_match: meta["description"] = desc_match.group(1).strip()[:500]
            
        logger.info(f"   Meta tags={meta['tags'][:3]}..., status={meta['status']}, poster={'OK' if meta['poster'] else 'MISS'}")
        return meta
    except Exception as e:
        logger.warning(f"   Failed to extract metadata for {slug}: {e}")
        return metadata


# ── play-fb-v8 resolver ─────────────────────────────────────────────────────
def resolve_play_fb_v8(proxy_url: str, retry_count: int = None) -> str | None:
    if retry_count is None: retry_count = CONFIG["RETRY_COUNT"]
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None

    last_error = None
    for attempt in range(retry_count + 1):
        try:
            req = urllib.request.Request(proxy_url, headers=PLAY_FB_V8_HEADERS, method="GET")
            opener = urllib.request.build_opener(NoRedirect())
            with opener.open(req, timeout=20) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if location and _is_valid_fb_cdn(location): return location
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    content = resp.read().decode('utf-8', errors='ignore')
                    if 'application/json' in content_type:
                        try:
                            data = json.loads(content)
                            url = data.get('url') or data.get('video_url') or data.get('stream_url') or data.get('src') or data.get('file')
                            if url and _is_valid_fb_cdn(url): return url
                        except json.JSONDecodeError: pass
                    url_patterns = [
                        r'"(https?://scontent-[^"]+\.mp4[^"]*)"', r"'(https?://scontent-[^']+\.mp4[^']*)'",
                        r'url\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']', r'src\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'file\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                    ]
                    for pattern in url_patterns:
                        match = re.search(pattern, content, re.IGNORECASE)
                        if match:
                            url = match.group(1).replace('\\/', '/')
                            if _is_valid_fb_cdn(url): return url
                    fallback = re.search(r'(https?://[^\s\'"]+fbcdn[^\s\'"]+\.mp4[^\s\'"]*)', content)
                    if fallback and _is_valid_fb_cdn(fallback.group(1)): return fallback.group(1)
                return None
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location", "")
                if location and _is_valid_fb_cdn(location): return location
            last_error = f"HTTPError {e.code}"
        except Exception as e:
            last_error = str(e)
        if attempt < retry_count:
            delay = CONFIG["RETRY_DELAY"] * (2 ** attempt)
            logger.debug(f"   Retry {attempt + 1}/{retry_count} in {delay}s...")
            time.sleep(delay)
    logger.warning(f"   resolve_play_fb_v8 failed: {last_error}")
    return None

def _is_valid_fb_cdn(url: str) -> bool:
    if not url: return False
    url_lower = url.lower()
    return ('fbcdn' in url_lower or 'facebook' in url_lower) and '.mp4' in url_lower


# ── List & Search Functions ──────────────────────────────────────────────────
def search_movies(page, keyword: str) -> list[dict]:
    try:
        search_url = f"{CONFIG['BASE_URL']}/tim-kiem?keyword={keyword}"
        page.goto(search_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        movies = page.evaluate("""() => {
            const results = [];
            const items = document.querySelectorAll('.flw-item');
            for (const item of items) {
                const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                if (!link?.href) continue;
                const slug = link.href.split('/').pop().replace(/\\/$/, '');
                const title = link.innerText.trim() || link.title || '';
                if (!title || slug.includes('search')) continue;
                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || 
                           item.querySelector('img[data-src], img.film-poster-img')?.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                results.push({ slug, title, thumb, badge });
            }
            return results;
        }""")
        logger.info(f"   Search '{keyword}': found {len(movies)} results")
        return movies
    except Exception as e:
        logger.error(f"   Search failed: {e}")
        return []

def list_all_movies(page, category_url: str = None) -> list[dict]:
    url = category_url or f"{CONFIG['BASE_URL']}/danh-sach/hoat-hinh"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        movies, page_num = [], 1
        while len(movies) < 100:
            batch = page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('.flw-item');
                for (const item of items) {
                    const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                    if (!link?.href) continue;
                    const slug = link.href.split('/').pop().replace(/\\/$/, '');
                    const title = link.innerText.trim() || link.title || '';
                    if (!title || slug.includes('search')) continue;
                    let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || 
                               item.querySelector('img[data-src], img.film-poster-img')?.src || '';
                    if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                    const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                    results.push({ slug, title, thumb, badge });
                }
                return results;
            }""")
            if not batch: break
            movies.extend(batch)
            next_btn = page.query_selector('a[title="Next"], .pagination li.active + li a')
            if not next_btn: break
            page_num += 1
            if page_num > 10: break
            next_btn.click()
            _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
            _human_delay(500, 1000)
        logger.info(f"   Listed {len(movies)} movies")
        return movies
    except Exception as e:
        logger.error(f"   List movies failed: {e}")
        return []

def get_trending_movies(page, limit: int = 50):
    """Fetch trending movies without hardcoded 10 limit"""
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, "homepage")
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        movies = page.evaluate(f"""() => {{
            const results = [];
            const items = document.querySelectorAll('.flw-item');
            for (const item of items) {{
                if (results.length >= {limit}) break;
                const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                if (!link?.href) continue;
                const slug = link.href.split('/').pop().replace(/\\/$/, '');
                const title = link.innerText.trim() || link.title || '';
                if (!title || slug.includes('search')) continue;
                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || 
                           item.querySelector('img[data-src], img.film-poster-img')?.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                results.push({{ slug, title, thumb, badge }});
            }}
            return results;
        }}""")
        return movies
    except Exception as e:
        logger.error(f"Failed to get trending movies: {e}")
        return []

def get_latest_ep_url(page, slug):
    detail_url = f"{CONFIG['BASE_URL']}/{slug}"
    try:
        _human_delay(300, 700)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, f"detail-{slug}")
        _wait_for_cf(page, f"a[href*='/{slug}/tap-']", CONFIG["TIMEOUT_WAIT"])
        return page.evaluate(f"""() => {{
            const links = Array.from(document.querySelectorAll('a[href*="/{slug}/tap-"]'))
                .filter(a => !a.href.includes('/sever2/'))
                .map(a => ({{ href: a.href, num: parseInt((a.href.match(/tap-(\\d+)/) || [])[1] || '0') }}))
                .filter(a => a.num > 0).sort((a, b) => b.num - a.num);
            return links.length ? links[0].href : null;
        }}""")
    except Exception as e:
        logger.warning(f"   Error on detail page for {slug}: {e}")
        return None

def get_episodes(page, slug):
    latest_url = get_latest_ep_url(page, slug)
    if not latest_url: return []
    try:
        _human_delay(400, 800)
        page.goto(latest_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, f"ep-{slug}")
        _wait_for_cf(page, "#episodes-content", CONFIG["TIMEOUT_WAIT"])
        episodes = page.evaluate("""() => {
            const results = [];
            const pane = document.querySelector('#top-comment');
            if (!pane) return results;
            const items = pane.querySelectorAll('a.ssl-item.ep-item');
            for (const item of items) {
                const href = item.href || '';
                const text = (item.querySelector('.ssli-order')?.innerText || item.querySelector('.ep-name')?.innerText || item.title || '').trim();
                if (href.includes('/sever2/')) continue;
                if (href && /^\\d+$/.test(text)) results.push({ name: text, url: href, num: parseInt(text) });
            }
            return results.sort((a, b) => b.num - a.num);
        }""")
        logger.info(f"   Got {len(episodes)} episodes (sorted: newest first)")
        return episodes
    except Exception as e:
        logger.warning(f"   Error at {latest_url}: {e}")
        return []

def get_stream_url(page, context, ep_url):
    try:
        _human_delay(200, 500)
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#list_sv", CONFIG["TIMEOUT_WAIT"])
        raw_buttons = page.evaluate("""() => {
            const results = [];
            const btns = document.querySelectorAll('#list_sv a.btn3dsv');
            for (const btn of btns) {
                const src = (btn.getAttribute('data-src') || '').trim();
                const label = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (src) results.push({ src, label });
            }
            return results;
        }""")
        preferred = [b for b in raw_buttons if any(x in b["label"] for x in ["hd", "1080", "4k"])]
        candidates = preferred if preferred else raw_buttons
        for btn in candidates:
            if "play-fb-v8" in btn["src"]:
                fb_cdn_url = resolve_play_fb_v8(btn["src"])
                if fb_cdn_url and _is_valid_fb_cdn(fb_cdn_url):
                    return [{"url": fb_cdn_url, "type": "mp4", "label": f"fb-cdn-{btn['label']}" if btn['label'] else "fb-cdn"}]
        return None
    except Exception as e:
        logger.debug(f"Stream extraction failed: {e}")
        return None


# ── JSON Builders ──────────────────────────────────────────────────────────
def build_detail_json(slug, episodes, metadata: dict = None):
    streams = []
    for i, ep in enumerate(episodes):
        raw_streams = ep.get("stream")
        if not raw_streams: continue
        stream_links = []
        for j, s in enumerate(raw_streams):
            stream_links.append({
                "id": f"{slug}--0-{i}-{j}",
                "name": s.get("label") or f"Link {j + 1}",
                "type": s["type"],
                "default": j == 0,
                "url": s["url"],
            })
        streams.append({"id": f"{slug}--0-{i}", "name": ep["name"], "stream_links": stream_links})
    
    result = {
        "sources": [{"id": f"{slug}--0", "name": "Thuyet Minh #1", "contents": [{"id": f"{slug}--0", "name": "", "grid_number": 3, "streams": streams}]}],
        "subtitle": "Thuyet Minh",
        "search": _build_search_str({"slug": slug, "title": slug}, metadata),
        "tags": metadata.get("tags", []) if metadata else [],
        "description": metadata.get("description", "") if metadata else "",
    }
    if metadata:
        if metadata.get("year"): result["year"] = metadata["year"]
        if metadata.get("status"): result["status"] = metadata["status"]
        if metadata.get("total_episodes"): result["total_episodes"] = metadata["total_episodes"]
    return result

def build_list_item(movie: dict, metadata: dict = None):
    # ✅ Use original movie dict for thumb & badge
    thumb = movie.get("thumb") or (metadata.get("poster") if metadata else "")
    badge = movie.get("badge") or metadata.get("status", "") if metadata else ""
    
    item = {
        "id": movie["slug"],
        "name": movie["title"],
        "description": metadata.get("description", "") if metadata else "",
        "search": _build_search_str(movie, metadata),
        "tags": metadata.get("tags", []) if metadata else [],
        "image": {"url": thumb, "type": "cover", "width": 480, "height": 640},
        "type": "playlist",
        "display": "text-below",
        "label": {"text": badge or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
        "remote_data": {"url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"},
        "enable_detail": True
    }
    if metadata:
        if metadata.get("year"): item["year"] = metadata["year"]
        if metadata.get("status"): item["status"] = metadata["status"]
    return item


# ── Scrape Single Movie ──────────────────────────────────────────────────────
def scrape_movie(page, context, movie_info: dict, max_episodes: int = None) -> tuple | None:
    """movie_info: dict chứa {slug, title, thumb, badge} từ homepage/list"""
    if max_episodes is None: max_episodes = CONFIG["MAX_EPISODES"]
    slug = movie_info["slug"]
    logger.info(f"  Processing: {slug}")
    
    metadata = get_movie_metadata(page, slug)
    # Merge homepage thumb/badge with detail metadata
    metadata["title"] = movie_info.get("title", slug)
    metadata["thumb"] = movie_info.get("thumb", "")
    metadata["badge"] = movie_info.get("badge", "")
    
    episodes = get_episodes(page, slug)
    if not episodes:
        logger.warning(f"  No episodes found for {slug}")
        return None

    ep_data = []
    crawl_limit = min(len(episodes), max_episodes)
    
    for i in range(crawl_limit):
        ep = episodes[i]
        _human_delay(CONFIG["EP_DELAY_MIN"], CONFIG["EP_DELAY_MAX"])
        
        stream = get_stream_url(page, context, ep["url"])
        if stream:
            ep_data.append({"name": ep["name"], "stream": stream})
            logger.info(f"    ✓ Tap {ep['name']}: OK")
        else:
            ep_data.append({
                "name": ep["name"],
                "stream": [{
                    "id": f"{slug}--0-{i}-err",
                    "name": f"{ep['name']}(no stream)",
                    "type": "error",
                    "default": False,
                    "url": "error:no_stream"
                }]
            })
            logger.warning(f"    ✗ Tap {ep['name']}: marked as no stream")
    
    detail_json = build_detail_json(slug, ep_data, metadata)
    list_item = build_list_item(movie_info, metadata)
    
    success_count = sum(1 for ep in ep_data if any(s["type"] != "error" for s in ep["stream"]))
    logger.info(f"  Saved {slug}.json ({success_count}/{crawl_limit} playable)")
    return list_item, detail_json


# ── CLI & Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="YanHH3D → MonPlayer Scraper v2.0")
    parser.add_argument("--search", type=str, help="Search movies by keyword")
    parser.add_argument("--slug", type=str, help="Scrape specific movie by slug")
    parser.add_argument("--url", type=str, help="Scrape movie from full URL")
    parser.add_argument("--list-all", action="store_true", help="List all movies from category")
    parser.add_argument("--trending", action="store_true", help="Scrape trending (default)")
    parser.add_argument("--max-movies", type=int, default=CONFIG["MAX_MOVIES"])
    parser.add_argument("--max-episodes", type=int, default=CONFIG["MAX_EPISODES"])
    parser.add_argument("--output", type=str, default=CONFIG["OUTPUT_DIR"])
    
    args = parser.parse_args()
    CONFIG["OUTPUT_DIR"] = args.output
    CONFIG["MAX_MOVIES"] = args.max_movies
    CONFIG["MAX_EPISODES"] = args.max_episodes
    
    logger.info(f"Starting YanHH3D to MonPlayer scraper (v2.0 - FIX THUMB/BADGE + NO 10-LIMIT)...")
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    channels = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--lang=vi-VN"])
        context = browser.new_context(user_agent=CONFIG["USER_AGENT"], viewport={"width": 1280, "height": 720}, locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh", extra_http_headers=EXTRA_HEADERS, java_script_enabled=True)
        page = context.new_page()
        _apply_stealth(page)

        try:
            # Helper to process list of movies
            def process_movie_list(movies):
                for movie in movies[:args.max_movies]:
                    try:
                        res = scrape_movie(page, context, movie, args.max_episodes)
                        if res:
                            li, dj = res
                            with open(detail_dir / f"{movie['slug']}.json", "w", encoding="utf-8") as f: 
                                json.dump(dj, f, ensure_ascii=False, indent=2)
                            channels.append(li)
                    except Exception as e:
                        logger.error(f"  Error processing {movie.get('slug', 'unknown')}: {e}")

            if args.search:
                movies = search_movies(page, args.search)
                process_movie_list(movies)
            elif args.slug:
                fake_movie = {"slug": args.slug, "title": args.slug, "thumb": "", "badge": ""}
                res = scrape_movie(page, context, fake_movie, args.max_episodes)
                if res:
                    li, dj = res
                    with open(detail_dir / f"{args.slug}.json", "w", encoding="utf-8") as f: json.dump(dj, f, ensure_ascii=False, indent=2)
                    channels.append(li)
            elif args.url:
                slug = args.url.rstrip('/').split('/')[-1]
                fake_movie = {"slug": slug, "title": slug, "thumb": "", "badge": ""}
                res = scrape_movie(page, context, fake_movie, args.max_episodes)
                if res:
                    li, dj = res
                    with open(detail_dir / f"{slug}.json", "w", encoding="utf-8") as f: json.dump(dj, f, ensure_ascii=False, indent=2)
                    channels.append(li)
            elif args.list_all:
                movies = list_all_movies(page)
                process_movie_list(movies)
            else:
                # ✅ Pass limit to get_trending_movies to bypass hardcoded 10
                movies = get_trending_movies(page, limit=max(50, args.max_movies))
                logger.info(f"Found {len(movies)} trending movies. Processing {min(len(movies), args.max_movies)}...")
                process_movie_list(movies)
                
        finally:
            browser.close()

    list_output = {
        "id": "yanhh3d-thuyet-minh", "name": "YanHH3D - Thuyet Minh", "url": f"{CONFIG['RAW_BASE']}/ophim",
        "color": "#004444", "image": {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyet minh chat luong cao tu YanHH3D.bz", "grid_number": 3, "channels": channels,
        "sorts": [{"text": "Moi nhat", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}],
        "meta": {"source": CONFIG["BASE_URL"], "total_items": len(channels), "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": "2.0"}
    }
    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f: json.dump(list_output, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Done! Saved {list_path} + {len(channels)} detail files.")

if __name__ == "__main__":
    main()
