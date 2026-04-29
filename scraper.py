#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v2.4 - PATCHED)
Fixes:
  - ✅ Fix JS SyntaxError: Thay Python ternary bằng JS ternary chuẩn
  - ✅ Fix Timeout: Thêm fallback selector & chờ Cloudflare challenge
  - ✅ Fix Parallel: Browser/Context lifecycle chuẩn Playwright async
  - ✅ Keep: Incremental, anti-rate-limit, search flags
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

try:
    from playwright_stealth import stealth_async
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
    "BASE_URL":             "https://yanhh3d.bz",
    "OUTPUT_DIR":           "ophim",
    "STATE_FILE":           "ophim/.state.json",
    "LIST_FILE":            "ophim.json",
    "MAX_MOVIES":           50,
    "MAX_EPISODES":         None,
    "TIMEOUT_NAV":          30000,
    "TIMEOUT_WAIT":         15000,
    "USER_AGENT":           "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":             os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"),
    "RETRY_COUNT":          2,
    "RETRY_DELAY":          1.0,
    "EP_DELAY_MIN":         1200,
    "EP_DELAY_MAX":         2200,
    "BATCH_SIZE":           10,
    "BATCH_COOLDOWN":       8.0,
    "CONSECUTIVE_FAIL_LIMIT": 5,
    "MAX_CONCURRENT_PAGES": 3,
    "INCREMENTAL_HOURS":    24,
}

EXTRA_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

PLAY_FB_V8_HEADERS = {
    "User-Agent": CONFIG["USER_AGENT"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    "Referer": "https://yanhh3d.bz/",
}


class ScraperState:
    def __init__(self, state_path: str):
        self.path = Path(state_path)
        self.data = self._load()
    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f: return json.load(f)
            except Exception: pass
        return {"movies": {}, "last_full_scan": None}
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f: json.dump(self.data, f, ensure_ascii=False, indent=2)
    def get_movie(self, slug: str) -> dict: return self.data["movies"].get(slug, {})
    def update_movie(self, slug: str, **kwargs):
        if slug not in self.data["movies"]: self.data["movies"][slug] = {}
        self.data["movies"][slug].update(kwargs)
        self.data["movies"][slug]["updated_at"] = datetime.now(timezone.utc).isoformat()
    def should_scrape(self, slug: str, incremental: bool) -> bool:
        if not incremental: return True
        last = self.get_movie(slug).get("last_scraped")
        if not last: return True
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))) < timedelta(hours=CONFIG["INCREMENTAL_HOURS"])
        except: return True
    def set_full_scan(self):
        self.data["last_full_scan"] = datetime.now(timezone.utc).isoformat()
        self.save()


def _build_search_str(movie: dict, metadata: dict = None) -> str:
    metadata = metadata or {}
    return " ".join(filter(None, [
        movie.get("title", ""), " ".join(metadata.get("tags", [])),
        metadata.get("description", ""), movie.get("slug", "").replace("-", " "),
        "hoạt hình trung quốc thuyết minh anime donghua"
    ])).lower()

def _is_valid_fb_cdn(url: str) -> bool:
    if not url: return False
    u = url.lower()
    return ('fbcdn' in u or 'facebook' in u) and '.mp4' in u


async def resolve_play_fb_v8_async(proxy_url: str) -> Optional[str]:
    for attempt in range(CONFIG["RETRY_COUNT"] + 1):
        try:
            req = urllib.request.Request(proxy_url, headers=PLAY_FB_V8_HEADERS, method="GET")
            with urllib.request.build_opener(urllib.request.HTTPRedirectHandler()).open(req, timeout=20) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location", "")
                    if loc and _is_valid_fb_cdn(loc): return loc
                if resp.status == 200:
                    content = resp.read().decode('utf-8', errors='ignore')
                    if 'application/json' in resp.headers.get('Content-Type', ''):
                        try:
                            d = json.loads(content)
                            url = d.get('url') or d.get('video_url') or d.get('stream_url') or d.get('src') or d.get('file')
                            if url and _is_valid_fb_cdn(url): return url
                        except: pass
                    for pat in [r'"(https?://scontent-[^"]+\.mp4[^"]*)"', r"'(https?://scontent-[^']+\.mp4[^']*)'",
                                r'url\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']', r'src\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']']:
                        m = re.search(pat, content, re.I)
                        if m:
                            url = m.group(1).replace('\\/', '/')
                            if _is_valid_fb_cdn(url): return url
                    fb = re.search(r'(https?://[^\s\'"]+fbcdn[^\s\'"]+\.mp4[^\s\'"]*)', content)
                    if fb and _is_valid_fb_cdn(fb.group(1)): return fb.group(1)
                return None
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                await asyncio.sleep(random.uniform(5, 12))
                continue
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location", "")
                if loc and _is_valid_fb_cdn(loc): return loc
        except Exception: pass
        if attempt < CONFIG["RETRY_COUNT"]: await asyncio.sleep(CONFIG["RETRY_DELAY"] * (2 ** attempt))
    return None


async def _wait_cf(page):
    try: await page.wait_for_function("() => !document.title.includes('Just a moment') && !document.querySelector('#challenge-running')", timeout=8000)
    except: pass


async def get_movie_metadata_async(page, slug: str) -> dict:
    meta = {"description": "", "tags": [], "year": "", "status": "", "poster": "", "total_episodes": ""}
    try:
        await page.goto(f"{CONFIG['BASE_URL']}/{slug}", wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        await _wait_cf(page)
        await page.wait_for_selector("body", timeout=10000)
        
        # ✅ ĐÃ SỬA CÚ PHÁP JS TERNARY
        m = await page.evaluate("""() => {
            const r = { description: "", tags: [], year: "", status: "", poster: "", total_episodes: "" };
            const desc = document.querySelector('meta[name="description"]')?.content || document.querySelector('meta[property="og:description"]')?.content || "";
            r.description = desc ? desc.trim().replace(/\s+/g, ' ').slice(0, 500) : "";
            
            document.querySelectorAll('.genres a, .film-info a[href*="/the-loai/"], .tick a').forEach(l => {
                const t = l.innerText.trim(); if (t && t.length < 50 && !/tap|tập/i.test(t)) r.tags.push(t);
            });
            r.tags = [...new Set(r.tags)].slice(0, 10);
            
            const y = document.title.match(/(\d{4})/) || document.querySelector('.film-info')?.innerText?.match(/(\d{4})/);
            if (y) r.year = y[1];
            
            const s = document.querySelector('.tick-rate, .badge, .status')?.innerText?.toLowerCase() || "";
            r.status = /hoàn thành|end|completed/i.test(s) ? "completed" : (/đang phát|ongoing|updating/i.test(s) ? "ongoing" : "");
            
            r.poster = document.querySelector('meta[property="og:image"]')?.content || document.querySelector('.film-poster img')?.src || document.querySelector('.film-poster img')?.dataset.src || "";
            const ep = document.querySelector('.total-episodes, .episode-count, .film-info .fdi-item')?.innerText || "";
            const em = ep.match(/(\d+)\s*(?:tập|ep)/i); if (em) r.total_episodes = em[1];
            return r;
        }""")
        meta.update(m)
    except Exception as e: logger.warning(f"   Metadata fail {slug}: {e}")
    return meta


async def get_episodes_async(page, slug: str) -> list:
    try:
        await page.goto(f"{CONFIG['BASE_URL']}/{slug}", wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        await _wait_cf(page)
        
        # ✅ FALLBACK SELECTORS nếu #top-comment đổi
        targets = ["#top-comment", ".ss-list", "#playlist", ".episodes-wrap", ".film-content"]
        active_sel = None
        for sel in targets:
            try:
                await page.wait_for_selector(sel, timeout=4000)
                active_sel = sel
                break
            except: continue
        
        if not active_sel:
            logger.warning(f"   Episode container not found for {slug}")
            return []
            
        return await page.evaluate(f"""() => {{
            const res = [];
            const pane = document.querySelector('{active_sel}'); if (!pane) return res;
            pane.querySelectorAll('a.ssl-item.ep-item, .ep-item, .episode-item, a[href*="tap-"]').forEach(item => {{
                const href = item.href || '';
                const text = (item.querySelector('.ssli-order')?.innerText || item.querySelector('.ep-name')?.innerText || item.innerText || item.title || '').trim();
                if (!href.includes('/sever2/') && /^\\d+$/.test(text)) res.push({{ name: text, url: href, num: parseInt(text) }});
            }});
            return res.sort((a, b) => b.num - a.num);
        }}""")
    except Exception as e: logger.warning(f"   Episode fetch fail {slug}: {e}")
    return []


async def get_stream_url_async(page, ep_url: str) -> Optional[list]:
    try:
        await page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        await _wait_cf(page)
        await page.wait_for_selector("#list_sv", timeout=10000)
        btns = await page.evaluate("""() => {
            const r = [];
            document.querySelectorAll('#list_sv a.btn3dsv').forEach(b => {
                const s = (b.getAttribute('data-src') || '').trim();
                const l = (b.innerText || b.textContent || '').trim().toLowerCase();
                if (s) r.push({ src: s, label: l });
            }); return r;
        }""")
        cands = [b for b in btns if any(x in b["label"] for x in ["hd", "1080", "4k"])] or btns
        for b in cands:
            if "play-fb-v8" in b["src"]:
                url = await resolve_play_fb_v8_async(b["src"])
                if url and _is_valid_fb_cdn(url):
                    return [{"url": url, "type": "mp4", "label": f"fb-cdn-{b['label']}" if b['label'] else "fb-cdn"}]
        return None
    except: return None


async def scrape_movie_async(page, movie_info: dict, state: ScraperState, max_episodes: int = None, incremental: bool = False) -> Optional[tuple]:
    if max_episodes is None: max_episodes = CONFIG["MAX_EPISODES"]
    slug = movie_info["slug"]
    if not state.should_scrape(slug, incremental):
        logger.info(f"  ⏭️ Skip {slug} (cached)")
        return None
    
    logger.info(f"  Processing: {slug}")
    metadata = await get_movie_metadata_async(page, slug)
    metadata.update({"title": movie_info.get("title", slug), "thumb": movie_info.get("thumb", ""), "badge": movie_info.get("badge", "")})
    
    episodes = await get_episodes_async(page, slug)
    if not episodes:
        logger.warning(f"  No episodes for {slug}")
        return None

    ep_data, crawl_limit, consec_fail = [], len(episodes) if max_episodes is None else min(len(episodes), max_episodes), 0
    for i in range(crawl_limit):
        ep = episodes[i]
        await asyncio.sleep(random.uniform(CONFIG["EP_DELAY_MIN"] / 1000, CONFIG["EP_DELAY_MAX"] / 1000))
        if (i + 1) % CONFIG["BATCH_SIZE"] == 0:
            await asyncio.sleep(CONFIG["BATCH_COOLDOWN"])
            consec_fail = 0
        
        stream = await get_stream_url_async(page, ep["url"])
        if stream:
            ep_data.append({"name": ep["name"], "stream": stream})
            consec_fail = 0
        else:
            consec_fail += 1
            ep_data.append({"name": ep["name"], "stream": [{"id": f"{slug}--0-{i}-err", "name": f"{ep['name']}(no stream)", "type": "error", "default": False, "url": "error:no_stream"}]})
            logger.warning(f"    ✗ Tap {ep['name']}: no stream (streak: {consec_fail})")
            if consec_fail >= CONFIG["CONSECUTIVE_FAIL_LIMIT"]: break
    
    state.update_movie(slug, last_scraped=datetime.now(timezone.utc).isoformat(), last_episode=episodes[0]["name"])
    return build_detail_json(slug, ep_data, metadata), build_list_item(movie_info, metadata)


def build_detail_json(slug, episodes, metadata: dict = None):
    metadata = metadata or {}
    streams = [{"id": f"{slug}--0-{i}", "name": ep["name"], "stream_links": [{"id": f"{slug}--0-{i}-{j}", "name": s.get("label") or f"Link {j+1}", "type": s["type"], "default": j==0, "url": s["url"]} for j, s in enumerate(ep.get("stream", []))]} for i, ep in enumerate(episodes) if ep.get("stream")]
    res = {"sources": [{"id": f"{slug}--0", "name": "Thuyet Minh #1", "contents": [{"id": f"{slug}--0", "name": "", "grid_number": 3, "streams": streams}]}], "subtitle": "Thuyet Minh", "search": _build_search_str({"slug": slug, "title": slug}, metadata), "tags": metadata.get("tags", []), "description": metadata.get("description", "")}
    for k in ["year", "status", "total_episodes"]:
        if metadata.get(k): res[k] = metadata[k]
    return res

def build_list_item(movie: dict, metadata: dict = None):
    metadata = metadata or {}
    return {"id": movie["slug"], "name": movie["title"], "search": _build_search_str(movie, metadata), "keywords": metadata.get("tags", []), "description": metadata.get("description", ""), "image": {"url": movie.get("thumb") or metadata.get("poster", ""), "type": "cover", "width": 480, "height": 640}, "type": "playlist", "display": "text-below", "label": {"text": movie.get("badge") or metadata.get("status", "") or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"}, "remote_data": {"url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"}, "enable_detail": True}


async def fetch_movies_async(page, args) -> list:
    url = f"{CONFIG['BASE_URL']}/tim-kiem?keyword={args.search}" if args.search else CONFIG["BASE_URL"]
    await page.goto(url, wait_until="domcontentloaded")
    await _wait_cf(page)
    limit = min(200, args.max_movies * 2)
    return await page.evaluate(f"""() => {{
        const r = [];
        document.querySelectorAll('.flw-item').forEach(item => {{
            if (r.length >= {limit}) return;
            const link = item.querySelector('.film-poster-ahref, .film-detail h3 a');
            if (!link?.href) return;
            const slug = link.href.split('/').pop().replace(/\\/$/, '');
            const title = link.innerText.trim() || link.title || '';
            if (!title || slug.includes('search')) return;
            let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || item.querySelector('img[data-src], img.film-poster-img')?.src || '';
            if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
            r.push({{ slug, title, thumb, badge: (item.querySelector('.tick.tick-rate, .fdi-item')?.innerText || '').trim() }});
        }}); return r;
    }}""")


async def process_movies_parallel(movies, state, args, browser):
    channels = []
    sem = asyncio.Semaphore(CONFIG["MAX_CONCURRENT_PAGES"])
    async def worker(movie):
        async with sem:
            ctx = await browser.new_context(user_agent=CONFIG["USER_AGENT"], viewport={"width": 1280, "height": 720}, locale="vi-VN", timezone_id="Asia/Ho_Chi_Minh", extra_http_headers=EXTRA_HEADERS)
            page = await ctx.new_page()
            if HAS_STEALTH: await stealth_async(page)
            try:
                res = await scrape_movie_async(page, movie, state, args.max_episodes, args.incremental and not args.full_scan)
                if res:
                    dj, li = res  # ✅ Đảo thứ tự trả về cho khớp hàm scrape
                    p = Path(CONFIG["OUTPUT_DIR"]) / "detail" / f"{movie['slug']}.json"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with open(p, "w", encoding="utf-8") as f: json.dump(dj, f, ensure_ascii=False, indent=2)
                    return li
            except Exception as e:
                logger.error(f"  ❌ Worker error {movie.get('slug')}: {e}")
            finally:
                await ctx.close()
            return None

    tasks = [worker(m) for m in movies[:args.max_movies]]
    for res in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(res, dict): channels.append(res)
        elif isinstance(res, Exception): logger.error(f"Task failed: {res}")
    return channels


async def main_async():
    parser = argparse.ArgumentParser()
    parser.add_argument("--search", type=str)
    parser.add_argument("--slug", type=str)
    parser.add_argument("--max-movies", type=int, default=CONFIG["MAX_MOVIES"])
    parser.add_argument("--max-episodes", type=int, default=None)
    parser.add_argument("--output", type=str, default=CONFIG["OUTPUT_DIR"])
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--full-scan", action="store_true")
    args = parser.parse_args()
    
    CONFIG.update({"OUTPUT_DIR": args.output, "MAX_MOVIES": args.max_movies, "MAX_EPISODES": args.max_episodes})
    incremental = args.incremental and not args.full_scan
    logger.info(f"Starting v2.4 (Incremental={incremental}, Parallel={CONFIG['MAX_CONCURRENT_PAGES']})")
    
    state = ScraperState(Path(CONFIG["OUTPUT_DIR"]) / ".state.json")
    Path(CONFIG["OUTPUT_DIR"]) / "detail".mkdir(parents=True, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(user_agent=CONFIG["USER_AGENT"], viewport={"width": 1280, "height": 720})
        page = await ctx.new_page()
        try:
            movies = await fetch_movies_async(page, args)
            logger.info(f"Found {len(movies)} movies.")
            if incremental:
                movies = [m for m in movies if state.should_scrape(m["slug"], True)]
                logger.info(f"Incremental filter: {len(movies)} to scrape.")
            channels = await process_movies_parallel(movies, state, args, browser) if movies else []
        finally:
            await browser.close()
    
    if args.full_scan: state.set_full_scan()
    else: state.save()
    
    list_out = {"id": "yanhh3d-thuyet-minh", "name": "YanHH3D - Thuyet Minh", "url": f"{CONFIG['RAW_BASE']}/ophim", "search": True, "enable_search": True, "features": {"search": True, "incremental": incremental}, "color": "#004444", "image": {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"}, "description": "Phim thuyet minh chat luong cao", "grid_number": 3, "channels": channels, "sorts": [{"text": "Moi nhat", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}], "meta": {"source": CONFIG["BASE_URL"], "total_items": len(channels), "updated_at": datetime.now(timezone.utc).isoformat(), "version": "2.4", "incremental": incremental}}
    with open(Path(CONFIG["LIST_FILE"]), "w", encoding="utf-8") as f: json.dump(list_out, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Done! {len(channels)} channels saved.")


def main(): asyncio.run(main_async())
if __name__ == "__main__": main()
