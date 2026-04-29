#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v1.7)
Features:
  - play-fb-v8 → Facebook CDN mp4 (DUY NHẤT)
  - Retry logic + fallback khi resolve fail
  - CLI: --search, --slug, --url, --list-all, --trending (default)
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
    "MAX_MOVIES":   5,
    "MAX_EPISODES": 2,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"),
    "RETRY_COUNT":  2,
    "RETRY_DELAY":  1.0,
}

QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd", "fb-cdn"]

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


# ── play-fb-v8 resolver (WITH RETRY) ─────────────────────────────────────────
def resolve_play_fb_v8(proxy_url: str, retry_count: int = None) -> str | None:
    """
    Follow redirect từ play-fb-v8 proxy → Facebook CDN mp4 thật
    Với retry logic + exponential backoff
    """
    if retry_count is None:
        retry_count = CONFIG["RETRY_COUNT"]
    
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    last_error = None
    
    for attempt in range(retry_count + 1):
        try:
            req = urllib.request.Request(proxy_url, headers=PLAY_FB_V8_HEADERS, method="GET")
            opener = urllib.request.build_opener(NoRedirect())
            
            with opener.open(req, timeout=20) as resp:
                # HTTP redirect
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if location and _is_valid_fb_cdn(location):
                        logger.info(f"   ✓ play-fb-v8 redirect → {location[:100]}...")
                        return location
                
                # 200 OK with JSON/HTML
                if resp.status == 200:
                    content_type = resp.headers.get('Content-Type', '')
                    content = resp.read().decode('utf-8', errors='ignore')
                    
                    # Parse JSON
                    if 'application/json' in content_type:
                        try:
                            data = json.loads(content)
                            url = (data.get('url') or data.get('video_url') or 
                                   data.get('stream_url') or data.get('src') or
                                   data.get('file'))
                            if url and _is_valid_fb_cdn(url):
                                logger.info(f"   ✓ play-fb-v8 JSON → {url[:100]}...")
                                return url
                        except json.JSONDecodeError:
                            pass
                    
                    # Parse HTML/JS - expanded patterns
                    url_patterns = [
                        r'"(https?://scontent-[^"]+\.mp4[^"]*)"',
                        r'"(https?://[^"]+fbcdn\.net[^"]+\.mp4[^"]*)"',
                        r"'(https?://scontent-[^']+\.mp4[^']*)'",
                        r"'(https?://[^']+fbcdn\.net[^']+\.mp4[^']*)'",
                        r'url\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'src\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'file\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                        r'data-url\s*=\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                    ]
                    for pattern in url_patterns:
                        match = re.search(pattern, content, re.IGNORECASE)
                        if match:
                            url = match.group(1).replace('\\/', '/')
                            if _is_valid_fb_cdn(url):
                                logger.info(f"   ✓ play-fb-v8 HTML parse → {url[:100]}...")
                                return url
                    
                    # Fallback: any fbcdn + mp4
                    fallback = re.search(r'(https?://[^\s\'"]+fbcdn[^\s\'"]+\.mp4[^\s\'"]*)', content)
                    if fallback and _is_valid_fb_cdn(fallback.group(1)):
                        logger.info(f"   ✓ play-fb-v8 fallback → {fallback.group(1)[:100]}...")
                        return fallback.group(1)
                
                return None
                
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location", "")
                if location and _is_valid_fb_cdn(location):
                    logger.info(f"   ✓ play-fb-v8 HTTPError {e.code} → {location[:100]}...")
                    return location
            last_error = f"HTTPError {e.code}"
        except Exception as e:
            last_error = str(e)
        
        # Retry with backoff
        if attempt < retry_count:
            delay = CONFIG["RETRY_DELAY"] * (2 ** attempt)
            logger.debug(f"   Retry {attempt + 1}/{retry_count} in {delay}s...")
            time.sleep(delay)
    
    logger.warning(f"   resolve_play_fb_v8 failed after {retry_count + 1} attempts: {last_error}")
    return None


def _is_valid_fb_cdn(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    return ('fbcdn' in url_lower or 'facebook' in url_lower) and '.mp4' in url_lower


# ── Search functionality ─────────────────────────────────────────────────────
def search_movies(page, keyword: str) -> list[dict]:
    """Search movies by keyword, return list of {slug, title, thumb}"""
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
                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                results.push({ slug, title, thumb });
            }
            return results;
        }""")
        logger.info(f"   Search '{keyword}': found {len(movies)} results")
        return movies
    except Exception as e:
        logger.error(f"   Search failed: {e}")
        return []


def list_all_movies(page, category_url: str = None) -> list[dict]:
    """List movies from category page or homepage"""
    url = category_url or f"{CONFIG['BASE_URL']}/danh-sach/hoat-hinh"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])

        movies = []
        page_num = 1
        
        while len(movies) < 100:  # Limit to avoid infinite loop
            batch = page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('.flw-item');
                for (const item of items) {
                    const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                    if (!link?.href) continue;
                    const slug = link.href.split('/').pop().replace(/\\/$/, '');
                    const title = link.innerText.trim() || link.title || '';
                    if (!title || slug.includes('search')) continue;
                    let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || '';
                    if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                    results.push({ slug, title, thumb });
                }
                return results;
            }""")
            
            if not batch:
                break
            movies.extend(batch)
            
            # Try next page
            next_btn = page.query_selector('a[title="Next"], .pagination li.active + li a')
            if not next_btn:
                break
            page_num += 1
            if page_num > 10:  # Max 10 pages
                break
            next_btn.click()
            _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
            _human_delay(500, 1000)
        
        logger.info(f"   Listed {len(movies)} movies from {url}")
        return movies
    except Exception as e:
        logger.error(f"   List movies failed: {e}")
        return []


# ── Step 1: Homepage → trending movies ────────────────────────────────────────
def get_trending_movies(page):
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, "homepage")
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])

        movies = page.evaluate("""() => {
            const results = [];
            const items = document.querySelectorAll('.flw-item');
            for (const item of items) {
                if (results.length >= 10) break;
                const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                if (!link?.href) continue;
                const slug = link.href.split('/').pop().replace(/\\/$/, '');
                const title = link.innerText.trim() || link.title || '';
                if (!title || slug.includes('search')) continue;
                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                results.push({ slug, title, thumb, badge });
            }
            return results;
        }""")
        return movies
    except Exception as e:
        logger.error(f"Failed to get trending movies: {e}")
        _debug_page(page, "homepage-error")
        return []


# ── Step 1b: Detail page → latest episode URL ─────────────────────────────────
def get_latest_ep_url(page, slug):
    detail_url = f"{CONFIG['BASE_URL']}/{slug}"
    try:
        _human_delay(300, 700)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, f"detail-{slug}")
        _wait_for_cf(page, f"a[href*='/{slug}/tap-']", CONFIG["TIMEOUT_WAIT"])

        latest_url = page.evaluate(f"""() => {{
            const links = Array.from(document.querySelectorAll('a[href*="/{slug}/tap-"]'))
                .filter(a => !a.href.includes('/sever2/'))
                .map(a => ({{
                    href: a.href,
                    num:  parseInt((a.href.match(/tap-(\\d+)/) || [])[1] || '0')
                }}))
                .filter(a => a.num > 0)
                .sort((a, b) => b.num - a.num);
            return links.length ? links[0].href : null;
        }}""")

        if latest_url:
            logger.info(f"   Latest ep URL: {latest_url}")
        else:
            logger.warning(f"   No tap- link found for {slug}")
        return latest_url

    except PlaywrightTimeout:
        logger.warning(f"   Timeout on detail page for {slug}")
        _debug_page(page, f"detail-timeout-{slug}")
        return None
    except Exception as e:
        logger.warning(f"   Error on detail page for {slug}: {e}")
        return None


# ── Step 2: Episode list (sorted newest first) ────────────────────────────────
def get_episodes(page, slug):
    latest_url = get_latest_ep_url(page, slug)
    if not latest_url:
        return []

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
                const text = (
                    item.querySelector('.ssli-order')?.innerText ||
                    item.querySelector('.ep-name')?.innerText ||
                    item.title || ''
                ).trim();
                if (href.includes('/sever2/')) continue;
                if (href && /^\\d+$/.test(text)) {
                    results.push({ name: text, url: href, num: parseInt(text) });
                }
            }
            return results.sort((a, b) => b.num - a.num);
        }""")

        if episodes:
            logger.info(f"   Got {len(episodes)} episodes from {latest_url} (sorted: newest first)")
        else:
            logger.warning(f"   No episodes found at {latest_url}")
            _debug_page(page, f"no-ep-list-{slug}")
        return episodes

    except PlaywrightTimeout:
        logger.warning(f"   Timeout at {latest_url}")
        return []
    except Exception as e:
        logger.warning(f"   Error at {latest_url}: {e}")
        return []


# ── Step 3: Stream extraction (WITH FALLBACK) ─────────────────────────────────
def get_stream_url(page, context, ep_url):
    """
    Trả về list stream:
      - play-fb-v8 → Facebook CDN mp4 (với retry)
      - Fallback: try next episode if current fails
    """
    try:
        _human_delay(200, 500)
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#list_sv", CONFIG["TIMEOUT_WAIT"])

        raw_buttons = page.evaluate("""() => {
            const results = [];
            const btns = document.querySelectorAll('#list_sv a.btn3dsv');
            for (const btn of btns) {
                const src   = (btn.getAttribute('data-src') || '').trim();
                const label = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (src) results.push({ src, label });
            }
            return results;
        }""")

        # Ưu tiên quality labels
        preferred = [b for b in raw_buttons if any(x in b["label"] for x in ["hd", "1080", "4k"])]
        candidates = preferred if preferred else raw_buttons

        for btn in candidates:
            src   = btn["src"]
            label = btn["label"]

            if "play-fb-v8" in src:
                logger.info(f"      Resolving play-fb-v8 ({label}): {src[:80]}...")
                fb_cdn_url = resolve_play_fb_v8(src)
                
                if fb_cdn_url and _is_valid_fb_cdn(fb_cdn_url):
                    return [{
                        "url":   fb_cdn_url,
                        "type":  "mp4",
                        "label": f"fb-cdn-{label}" if label else "fb-cdn",
                    }]
                else:
                    logger.warning(f"      ✗ play-fb-v8 resolve failed, trying next source...")
        
        logger.warning(f"      ✗ No valid play-fb-v8 stream found")
        return None

    except Exception as e:
        logger.debug(f"Stream extraction failed for {ep_url}: {e}")
        _debug_page(page, "stream-error")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _sort_streams(stream_list):
    return stream_list


def build_detail_json(slug, episodes):
    streams = []
    for i, ep in enumerate(episodes):
        raw_streams = ep.get("stream")
        if not raw_streams:
            continue
        sorted_streams = _sort_streams(raw_streams)
        stream_links = []
        for j, s in enumerate(sorted_streams):
            label = s.get("label") or f"Link {j + 1}"
            stream_links.append({
                "id":      f"{slug}--0-{i}-{j}",
                "name":    label,
                "type":    s["type"],
                "default": j == 0,
                "url":     s["url"],
            })
        streams.append({
            "id":           f"{slug}--0-{i}",
            "name":         ep["name"],
            "stream_links": stream_links
        })
    return {
        "sources": [{
            "id":   f"{slug}--0",
            "name": "Thuyet Minh #1",
            "contents": [{
                "id":          f"{slug}--0",
                "name":        "",
                "grid_number": 3,
                "streams":     streams
            }]
        }],
        "subtitle": "Thuyet Minh"
    }


def build_list_item(movie):
    return {
        "id":          movie["slug"],
        "name":        movie["title"],
        "description": "",
        "image": {
            "url":    movie["thumb"],
            "type":   "cover",
            "width":  480,
            "height": 640
        },
        "type":    "playlist",
        "display": "text-below",
        "label": {
            "text":       movie.get("badge") or "Trending",
            "position":   "top-left",
            "color":      "#35ba8b",
            "text_color": "#ffffff"
        },
        "remote_data": {
            "url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"
        },
        "enable_detail": True
    }


# ── Scrape single movie ───────────────────────────────────────────────────────
def scrape_movie(page, context, slug: str, max_episodes: int = None) -> dict | None:
    """Scrape a single movie by slug, return detail JSON"""
    if max_episodes is None:
        max_episodes = CONFIG["MAX_EPISODES"]
    
    logger.info(f"  Processing: {slug}")
    
    episodes = get_episodes(page, slug)
    if not episodes:
        logger.warning(f"  No episodes found for {slug}")
        return None

    ep_data = []
    crawl_limit = min(len(episodes), max_episodes)
    
    for i in range(crawl_limit):
        ep = episodes[i]
        stream = get_stream_url(page, context, ep["url"])
        if stream:
            ep_data.append({"name": ep["name"], "stream": stream})
            logger.info(f"    ✓ Tap {ep['name']}: OK")
        else:
            logger.warning(f"    ✗ Tap {ep['name']}: no stream")
    
    if not ep_data:
        logger.warning(f"  No valid streams for {slug}")
        return None
    
    detail_json = build_detail_json(slug, ep_data)
    logger.info(f"  Saved {slug}.json ({len(ep_data)}/{crawl_limit} episodes)")
    return build_list_item({"slug": slug, "title": slug, "thumb": "", "badge": ""}), detail_json


# ── Main scraper ──────────────────────────────────────────────────────────────
def scrape_trending(page, context, max_movies: int = None) -> list:
    """Scrape trending movies (default behavior)"""
    if max_movies is None:
        max_movies = CONFIG["MAX_MOVIES"]
    
    channels = []
    movies = get_trending_movies(page)
    if not movies:
        logger.error("No movies found. Exiting.")
        return channels

    limit = min(len(movies), max_movies)
    logger.info(f"Found {len(movies)} trending movies. Processing {limit}...")

    for idx, movie in enumerate(movies[:limit], 1):
        logger.info(f"[{idx}/{limit}] {movie['title']} ({movie['slug']})")
        try:
            result = scrape_movie(page, context, movie["slug"])
            if result:
                list_item, detail_json = result
                # Save detail file
                detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
                detail_dir.mkdir(parents=True, exist_ok=True)
                detail_path = detail_dir / f"{movie['slug']}.json"
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(detail_json, f, ensure_ascii=False, indent=2)
                channels.append(list_item)
        except Exception as e:
            logger.error(f"  Error processing {movie['slug']}: {e}")
            continue
    
    return channels


# ── CLI Entry Point ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="YanHH3D → MonPlayer Scraper v1.7")
    parser.add_argument("--search", type=str, help="Search movies by keyword")
    parser.add_argument("--slug", type=str, help="Scrape specific movie by slug")
    parser.add_argument("--url", type=str, help="Scrape movie from full URL")
    parser.add_argument("--list-all", action="store_true", help="List all movies from category")
    parser.add_argument("--trending", action="store_true", help="Scrape trending (default)")
    parser.add_argument("--max-movies", type=int, default=CONFIG["MAX_MOVIES"], help="Max movies to scrape")
    parser.add_argument("--max-episodes", type=int, default=CONFIG["MAX_EPISODES"], help="Max episodes per movie")
    parser.add_argument("--output", type=str, default=CONFIG["OUTPUT_DIR"], help="Output directory")
    
    args = parser.parse_args()
    
    # Update config from args
    CONFIG["OUTPUT_DIR"] = args.output
    CONFIG["MAX_MOVIES"] = args.max_movies
    CONFIG["MAX_EPISODES"] = args.max_episodes
    
    logger.info(f"Starting YanHH3D to MonPlayer scraper (v1.7 - CLI + RETRY)...")
    logger.info(f"playwright-stealth: {'OK' if HAS_STEALTH else 'NOT FOUND'}")
    
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    channels = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--lang=vi-VN"]
        )
        context = browser.new_context(
            user_agent=CONFIG["USER_AGENT"],
            viewport={"width": 1280, "height": 720},
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            extra_http_headers=EXTRA_HEADERS,
            java_script_enabled=True,
        )
        page = context.new_page()
        _apply_stealth(page)

        try:
            # CLI routing
            if args.search:
                movies = search_movies(page, args.search)
                for movie in movies[:args.max_movies]:
                    result = scrape_movie(page, context, movie["slug"])
                    if result:
                        list_item, detail_json = result
                        detail_path = detail_dir / f"{movie['slug']}.json"
                        with open(detail_path, "w", encoding="utf-8") as f:
                            json.dump(detail_json, f, ensure_ascii=False, indent=2)
                        channels.append(list_item)
            
            elif args.slug:
                result = scrape_movie(page, context, args.slug, args.max_episodes)
                if result:
                    list_item, detail_json = result
                    detail_path = detail_dir / f"{args.slug}.json"
                    with open(detail_path, "w", encoding="utf-8") as f:
                        json.dump(detail_json, f, ensure_ascii=False, indent=2)
                    channels.append(list_item)
            
            elif args.url:
                # Extract slug from URL
                slug = args.url.rstrip('/').split('/')[-1]
                result = scrape_movie(page, context, slug, args.max_episodes)
                if result:
                    list_item, detail_json = result
                    detail_path = detail_dir / f"{slug}.json"
                    with open(detail_path, "w", encoding="utf-8") as f:
                        json.dump(detail_json, f, ensure_ascii=False, indent=2)
                    channels.append(list_item)
            
            elif args.list_all:
                movies = list_all_movies(page)
                for movie in movies[:args.max_movies]:
                    result = scrape_movie(page, context, movie["slug"])
                    if result:
                        list_item, detail_json = result
                        detail_path = detail_dir / f"{movie['slug']}.json"
                        with open(detail_path, "w", encoding="utf-8") as f:
                            json.dump(detail_json, f, ensure_ascii=False, indent=2)
                        channels.append(list_item)
            
            else:  # Default: trending
                channels = scrape_trending(page, context, args.max_movies)
        
        finally:
            browser.close()

    # Save list file
    list_output = {
        "id":          "yanhh3d-thuyet-minh",
        "name":        "YanHH3D - Thuyet Minh",
        "url":         f"{CONFIG['RAW_BASE']}/ophim",
        "color":       "#004444",
        "image":       {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyet minh chat luong cao tu YanHH3D.bz",
        "grid_number": 3,
        "channels":    channels,
        "sorts":       [{"text": "Moi nhat", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}],
        "meta": {
            "source":      CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version":     "1.7"
        }
    }

    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ Done! Saved {list_path} + {len(channels)} detail files.")
    return list_output


if __name__ == "__main__":
    main()
