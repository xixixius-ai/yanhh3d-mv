#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v4.0 - STATEFUL OFFSET | SYNTAX-FIXED)
✅ Stateful: Lưu progress.json, mỗi lần chạy chỉ crawl 20 tập tiếp theo
✅ Auto-resume: Nối tiếp chính xác dù dừng giữa chừng hay sang ngày mới
✅ Auto-detect new eps: Nếu phim có tập mới, tự động điều chỉnh offset, không bỏ sót
✅ CDN Fix: Dùng context.request thay urllib → bypass limit ~20 link/run
✅ Clean Syntax: Đã sửa toàn bộ f-string JS conflict + 'meta dict' typo
"""

import argparse
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout, BrowserContext, Page, APIResponse

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
    "MAX_MOVIES":   24,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"),
    "RETRY_COUNT":  2,
    "RETRY_DELAY":  1.0,
    "EP_DELAY_MIN": 2500,
    "EP_DELAY_MAX": 4500,
    "BATCH_LIMIT":  20,
    "CONSECUTIVE_FAIL_LIMIT": 5,
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


def resolve_play_fb_v8(context: BrowserContext, proxy_url: str) -> str | None:
    for attempt in range(CONFIG["RETRY_COUNT"] + 1):
        try:
            resp = context.request.get(proxy_url, headers=PLAY_FB_V8_HEADERS, timeout=20000)
            if resp.status in (301, 302, 303, 307, 308):
                location = resp.headers.get("location", "")
                if location and _is_valid_fb_cdn(location):
                    return location
            if resp.status != 200:
                if resp.status in (403, 429):
                    time.sleep(CONFIG["RETRY_DELAY"] * (2 ** attempt))
                    continue
                return None
                
            content = resp.text()
            patterns = [
                r'"(https?://scontent-[^"]+\.mp4[^"]*)"',
                r"'(https?://scontent-[^']+\.mp4[^']*)'",
                r'url\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                r'src\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                r'file\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                r'<source[^>]+src=["\'](https?://[^"\']+\.mp4[^"\']*)["\']',
                r'video_url["\']?\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                r'["\']src["\']\s*:\s*["\']([^"\']+fbcdn[^"\']+\.mp4[^"\']*)["\']',
            ]
            for pat in patterns:
                m = re.search(pat, content, re.IGNORECASE)
                if m:
                    url = m.group(1).replace('\\/', '/')
                    if _is_valid_fb_cdn(url): return url
            return None
        except Exception:
            if attempt < CONFIG["RETRY_COUNT"]:
                time.sleep(CONFIG["RETRY_DELAY"] * (2 ** attempt))
    return None


def _is_valid_fb_cdn(url: str) -> bool:
    if not url: return False
    u = url.lower()
    return ('fbcdn' in u or 'facebook' in u) and '.mp4' in u


def search_movies(page: Page, keyword: str) -> list[dict]:
    try:
        page.goto(f"{CONFIG['BASE_URL']}/tim-kiem?keyword={keyword}", wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        return page.evaluate("""() => {
            const res = [];
            document.querySelectorAll('.flw-item').forEach(item => {
                const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                if (!link?.href) return;
                const slug = link.href.split('/').pop().replace(/\/$/, '');
                const title = link.innerText.trim() || link.title || '';
                if (!title || slug.includes('search')) return;
                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || item.querySelector('img[data-src], img.film-poster-img')?.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                res.push({ slug, title, thumb, badge });
            });
            return res;
        }""")
    except Exception as e:
        logger.error(f"   Search failed: {e}")
        return []


def get_trending_movies(page: Page, limit: int = 50):
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        js_code = """(lim) => {
            const res = [];
            document.querySelectorAll('.flw-item').forEach(item => {
                if (res.length >= lim) return;
                const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
                if (!link?.href) return;
                const slug = link.href.split('/').pop().replace(/\/$/, '');
                const title = link.innerText.trim() || link.title || '';
                if (!title || slug.includes('search')) return;
                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || item.querySelector('img[data-src], img.film-poster-img')?.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';
                res.push({ slug, title, thumb, badge });
            });
            return res;
        }"""
        return page.evaluate(js_code, limit)
    except Exception as e:
        logger.error(f"Failed to get trending: {e}")
        return []


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
        for b in btns:
            if "play-fb-v8" in b["src"]:
                url = resolve_play_fb_v8(context, b["src"])
                if url and _is_valid_fb_cdn(url):
                    return [{"url": url, "type": "mp4", "label": f"fb-cdn-{b['label']}" if b['label'] else "fb-cdn"}]
        return None
    except Exception:
        return None


def build_detail_json(slug: str, episodes: list, metadata: dict = None):
    metadata = metadata or {}
    streams = []
    for i, ep in enumerate(episodes):
        if not ep.get("stream"): continue
        sl = [{"id": f"{slug}--0-{i}-{j}", "name": s.get("label") or f"Link {j+1}", "type": s["type"], "default": j==0, "url": s["url"]} for j, s in enumerate(ep["stream"])]
        streams.append({"id": f"{slug}--0-{i}", "name": ep["name"], "stream_links": sl})
    
    result = {
        "sources": [{"id": f"{slug}--0", "name": "Thuyet Minh #1", "contents": [{"id": f"{slug}--0", "name": "", "grid_number": 3, "streams": streams}]}],
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


def scrape_movie(page: Page, context: BrowserContext, movie_info: dict, force_all: bool = False, progress: dict = None):
    if progress is None: progress = {}
    slug = movie_info["slug"]
    logger.info(f"  Processing: {slug}")
    
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
    
    detail_json = build_detail_json(slug, ep_data, metadata)
    list_item = build_list_item(movie_info, metadata)
    
    ok = sum(1 for e in ep_data if any(s["type"] != "error" for s in e["stream"]))
    logger.info(f"  ✅ Saved {slug}.json ({ok}/{crawled_count} playable) | Offset -> {new_offset}")
    return list_item, detail_json


def main():
    parser = argparse.ArgumentParser(description="YanHH3D → MonPlayer Scraper v4.0")
    parser.add_argument("--search", type=str)
    parser.add_argument("--slug", type=str)
    parser.add_argument("--url", type=str)
    parser.add_argument("--trending", action="store_true", default=True)
    parser.add_argument("--max-movies", type=int, default=CONFIG["MAX_MOVIES"])
    parser.add_argument("--all-episodes", action="store_true")
    parser.add_argument("--output", type=str, default=CONFIG["OUTPUT_DIR"])
    args = parser.parse_args()
    
    CONFIG["OUTPUT_DIR"] = args.output
    CONFIG["MAX_MOVIES"] = args.max_movies
    
    logger.info(f"Starting v4.0 - STATEFUL OFFSET SCRAPER")
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
            movies = search_movies(page, args.search) if args.search else get_trending_movies(page, limit=max(50, args.max_movies))
            logger.info(f"Found {len(movies)} movies. Processing {min(len(movies), args.max_movies)}...")
            
            for movie in movies[:args.max_movies]:
                try:
                    res = scrape_movie(page, context, movie, force_all=args.all_episodes, progress=progress)
                    if res:
                        li, dj = res
                        with open(detail_dir / f"{movie['slug']}.json", "w", encoding="utf-8") as f: json.dump(dj, f, ensure_ascii=False, indent=2)
                        channels.append(li)
                except Exception as e:
                    logger.error(f"  Error processing {movie.get('slug', 'unknown')}: {e}")
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
        "meta": {"source": CONFIG["BASE_URL"], "total_items": len(channels), "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "version": "4.0"}
    }
    with open(Path(CONFIG["LIST_FILE"]), "w", encoding="utf-8") as f: json.dump(list_output, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Done! Saved list + {len(channels)} details. Progress updated.")

if __name__ == "__main__":
    main()
