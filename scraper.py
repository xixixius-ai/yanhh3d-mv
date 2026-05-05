#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v5.0 - Browser Fingerprint Injection)
✅ Giải quyết triệt để bài toán giới hạn 20 tập/phim
✅ Dùng Playwright Route Interception: Lấy link MP4 bằng đúng Fingerprint của Chrome
✅ Bỏ hẳn urllib cho play-fb-v8 -> Không bị nhận diện là Bot Python
✅ Zero-bandwidth: Bắt link MP4 từ Header Redirect mà không tải video về
✅ Hiển thị Progress: [Phim 3/24]
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
    "MAX_MOVIES":   24,
    "MAX_EPISODES": None,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"),
    "EP_DELAY_MIN": 2000,
    "EP_DELAY_MAX": 4000,
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


def _is_valid_fb_cdn(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    return ('fbcdn' in url_lower or 'facebook' in url_lower) and '.mp4' in url_lower


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
            result.description = desc ? desc.trim().replace(/\\s+/g, ' ').slice(0, 500) : "";
            
            const genreLinks = document.querySelectorAll('.genres a, .film-info a[href*="/the-loai/"], .tick a');
            for (const link of genreLinks) {
                const text = link.innerText.trim();
                if (text && text.length < 50 && !/tap|tập/i.test(text)) result.tags.push(text);
            }
            result.tags = [...new Set(result.tags)].slice(0, 10);
            
            const yearMatch = document.title.match(/(\\d{4})/) || document.querySelector('.film-info')?.innerText?.match(/(\\d{4})/);
            if (yearMatch) result.year = yearMatch[1];
            
            const statusText = document.querySelector('.tick-rate, .badge, .status')?.innerText?.toLowerCase() || "";
            if (/hoàn thành|end|completed/i.test(statusText)) result.status = "completed";
            else if (/đang phát|ongoing|updating/i.test(statusText)) result.status = "ongoing";
            
            const poster = document.querySelector('meta[property="og:image"]')?.content ||
                          document.querySelector('.film-poster img')?.src ||
                          document.querySelector('.film-poster img')?.dataset.src || "";
            result.poster = poster || "";
            
            const epInfo = document.querySelector('.total-episodes, .episode-count, .film-info .fdi-item')?.innerText || "";
            const epMatch = epInfo.match(/(\\d+)\\s*(?:tập|ep)/i);
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


def get_stream_url(page: Page, ep_url: str):
    """
    🔥 CORE LOGIC V5.0: Browser Fingerprint Injection
    Thay vì dùng urllib (bị phát hiện là bot và chặn 20 link), 
    ta dùng Playwright để bắt request, dùng Chrome fingerprint đi lấy link, 
    đọc header redirect rồi hủy request ngay để không tải video.
    """
    intercepted_url = None
    
    def handle_proxy_request(route):
        nonlocal intercepted_url
        try:
            # route.fetch() dùng chính TLS fingerprint của trình duyệt Chrome để gọi API
            response = route.fetch()
            
            # Case 1: Server trả về Redirect (302) sang fbcdn.net
            # response.url sẽ tự động là URL cuối cùng sau khi redirect
            if _is_valid_fb_cdn(response.url):
                intercepted_url = response.url
                route.abort() # Chặn không cho trình duyệt tải file mp4 nặng
                return
                
            # Case 2: Server trả về JSON chứa link trực tiếp
            if response.status == 200 and 'application/json' in response.headers.get('content-type', ''):
                # response.json() chỉ tải body của JSON (vài KB), không tải video
                data = response.json()
                url = data.get('url') or data.get('video_url') or data.get('stream_url') or data.get('src') or data.get('file')
                if url and _is_valid_fb_cdn(url):
                    intercepted_url = url
                    route.abort()
                    return
                    
        except Exception as e:
            pass
            
        # Chặn các request rác khác (ảnh, css) đi kèm để tiết kiệm RAM
        route.abort()

    try:
        # Bắt mọi request khớp pattern play-fb-v8
        page.route("**/play-fb-v8**", handle_proxy_request)
        
        _human_delay(200, 500)
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#list_sv", CONFIG["TIMEOUT_WAIT"])
        
        # Kích hoạt trình duyệt gọi API (bằng cách click vào nút server)
        btn = page.query_selector("#list_sv a.btn3dsv[data-src*='play-fb-v8']")
        if not btn:
            btn = page.query_selector("#list_sv a.btn3dsv[href*='play-fb-v8']")
            
        if btn:
            btn.click()
            page.wait_for_timeout(2000) # Đợi JS thực thi xong request
        else:
            page.wait_for_timeout(1000)
            
        # Gỡ bộ lọc
        page.unroute("**/play-fb-v8**")
        
        if intercepted_url:
            return [{"url": intercepted_url, "type": "mp4", "label": "fb-cdn-chrome"}]
            
        return None
        
    except Exception as e:
        logger.debug(f"Stream extraction failed: {e}")
        return None
    finally:
        try:
            page.unroute("**/play-fb-v8**")
        except:
            pass


def list_all_movies(page: Page, category_url: str = None) -> list[dict]:
    url = category_url or f"{CONFIG['BASE_URL']}/danh-sach/hoat-hinh"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        movies, page_num = [], 1
        while len(movies) < 200:
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
            if not batch:
                break
            movies.extend(batch)
            next_btn = page.query_selector('a[title="Next"], .pagination li.active + li a')
            if not next_btn:
                break
            page_num += 1
            if page_num > 15:
                break
            next_btn.click()
            _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
            _human_delay(500, 1000)
        return movies
    except Exception as e:
        logger.error(f"   List movies failed: {e}")
        return []


def get_trending_movies(page: Page, limit: int = 50):
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        return page.evaluate(f"""() => {{
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
    except Exception as e:
        logger.error(f"Failed to get trending movies: {e}")
        return []


def get_latest_ep_url(page: Page, slug: str):
    detail_url = f"{CONFIG['BASE_URL']}/{slug}"
    try:
        _human_delay(300, 700)
        page.goto(detail_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
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


def get_episodes(page: Page, slug: str):
    latest_url = get_latest_ep_url(page, slug)
    if not latest_url:
        return []
    try:
        _human_delay(400, 800)
        page.goto(latest_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
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
        logger.info(f"   Found {len(episodes)} episodes")
        return episodes
    except Exception as e:
        logger.warning(f"   Error at {latest_url}: {e}")
        return []


def build_detail_json(slug: str, episodes: list, metadata: dict = None):
    metadata = metadata or {}
    streams = []
    for i, ep in enumerate(episodes):
        raw_streams = ep.get("stream")
        if not raw_streams:
            continue
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
        "sources": [{
            "id": f"{slug}--0",
            "name": "Thuyet Minh #1",
            "contents": [{
                "id": f"{slug}--0",
                "name": "",
                "grid_number": 3,
                "streams": streams
            }]
        }],
        "subtitle": "Thuyet Minh",
        "search": _build_search_str({"slug": slug, "title": slug}, metadata),
        "tags": metadata.get("tags", []),
        "description": metadata.get("description", ""),
    }
    if metadata.get("year"):
        result["year"] = metadata["year"]
    if metadata.get("status"):
        result["status"] = metadata["status"]
    if metadata.get("total_episodes"):
        result["total_episodes"] = metadata["total_episodes"]
    return result


def build_list_item(movie: dict, metadata: dict = None):
    metadata = metadata or {}
    thumb = movie.get("thumb") or metadata.get("poster", "")
    badge = movie.get("badge") or metadata.get("status", "")
    
    item = {
        "id": movie["slug"],
        "name": movie["title"],
        "search": _build_search_str(movie, metadata),
        "keywords": metadata.get("tags", []),
        "description": metadata.get("description", ""),
        "image": {"url": thumb, "type": "cover", "width": 480, "height": 640},
        "type": "playlist",
        "display": "text-below",
        "label": {"text": badge or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
        "remote_data": {"url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"},
        "enable_detail": True
    }
    if metadata.get("year"):
        item["year"] = metadata["year"]
    if metadata.get("status"):
        item["status"] = metadata["status"]
    return item


def scrape_movie(page: Page, movie_info: dict, movie_index: int, total_movies: int,
                 max_episodes: int = None, force_all_episodes: bool = False) -> tuple | None:
    if max_episodes is None:
        max_episodes = CONFIG["MAX_EPISODES"]
    slug = movie_info["slug"]
    
    # 🔥 HIỂN THỊ TIẾN TRÌNH
    logger.info(f"  [{movie_index}/{total_movies}] Processing: {slug}")
    
    metadata = get_movie_metadata(page, slug)
    metadata["title"] = movie_info.get("title", slug)
    metadata["thumb"] = movie_info.get("thumb", "")
    metadata["badge"] = movie_info.get("badge", "")
    
    episodes = get_episodes(page, slug)
    if not episodes:
        logger.warning(f"  [{movie_index}/{total_movies}] Skipped {slug}: No episodes found")
        return None

    ep_data = []
    crawl_limit = len(episodes)
    
    if force_all_episodes:
        logger.info(f"  [{movie_index}/{total_movies}] {slug}: Crawling ALL {crawl_limit} episodes")
    elif max_episodes is not None:
        crawl_limit = min(len(episodes), max_episodes)
        logger.info(f"  [{movie_index}/{total_movies}] {slug}: Custom limit -> {crawl_limit} eps")
    else:
        logger.info(f"  [{movie_index}/{total_movies}] {slug}: Crawling ALL {crawl_limit} episodes")
    
    for i in range(crawl_limit):
        ep = episodes[i]
        _human_delay(CONFIG["EP_DELAY_MIN"], CONFIG["EP_DELAY_MAX"])
        
        stream = get_stream_url(page, ep["url"])
        if stream:
            ep_data.append({"name": ep["name"], "stream": stream})
            logger.info(f"  [{movie_index}/{total_movies}]    ✓ Tap {ep['name']}")
        else:
            logger.warning(f"  [{movie_index}/{total_movies}]    ✗ Tap {ep['name']} (Failed to extract, skipping)")
                    
    if not ep_data:
        logger.error(f"  [{movie_index}/{total_movies}] Skipped {slug}: 0 playable episodes extracted")
        return None

    detail_json = build_detail_json(slug, ep_data, metadata)
    list_item = build_list_item(movie_info, metadata)
    
    logger.info(f"  [{movie_index}/{total_movies}] ✅ Saved {slug}.json ({len(ep_data)} playable)")
    return list_item, detail_json


def main():
    parser = argparse.ArgumentParser(description="YanHH3D → MonPlayer Scraper v5.0 (Fingerprint Injection)")
    parser.add_argument("--slug", type=str, help="Scrape specific movie by slug")
    parser.add_argument("--url", type=str, help="Scrape movie from full URL")
    parser.add_argument("--list-all", action="store_true", help="List all movies from category")
    parser.add_argument("--trending", action="store_true", help="Scrape trending (default)")
    parser.add_argument("--max-movies", type=int, default=CONFIG["MAX_MOVIES"])
    parser.add_argument("--max-episodes", type=int, default=None, help="Max episodes per movie (None = ALL)")
    parser.add_argument("--all-episodes", action="store_true", help="Crawl ALL episodes")
    parser.add_argument("--output", type=str, default=CONFIG["OUTPUT_DIR"])
    
    args = parser.parse_args()
    CONFIG["OUTPUT_DIR"] = args.output
    CONFIG["MAX_MOVIES"] = args.max_movies
    if not args.all_episodes and args.max_episodes is not None:
        CONFIG["MAX_EPISODES"] = args.max_episodes
    
    logger.info(f"Starting YanHH3D Scraper (v5.0 - Browser Fingerprint Injection)...")
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    channels = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--lang=vi-VN"])
        
        context = browser.new_context(
            user_agent=CONFIG["USER_AGENT"],
            viewport={"width": 1280, "height": 720},
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            extra_http_headers=EXTRA_HEADERS,
            java_script_enabled=True
        )
        page = context.new_page()
        _apply_stealth(page)

        try:
            def process_movie_list(movies):
                total = min(len(movies), args.max_movies)
                for idx, movie in enumerate(movies[:args.max_movies], start=1):
                    try:
                        res = scrape_movie(page, movie, idx, total, 
                                         max_episodes=args.max_episodes, 
                                         force_all_episodes=args.all_episodes)
                        if res:
                            li, dj = res
                            with open(detail_dir / f"{movie['slug']}.json", "w", encoding="utf-8") as f: 
                                json.dump(dj, f, ensure_ascii=False, indent=2)
                            channels.append(li)
                    except Exception as e:
                        logger.error(f"  [{idx}/{total}] Error processing {movie.get('slug', 'unknown')}: {e}")

            if args.slug:
                fake_movie = {"slug": args.slug, "title": args.slug, "thumb": "", "badge": ""}
                res = scrape_movie(page, fake_movie, 1, 1, 
                                 max_episodes=args.max_episodes, 
                                 force_all_episodes=args.all_episodes)
                if res:
                    li, dj = res
                    with open(detail_dir / f"{args.slug}.json", "w", encoding="utf-8") as f: 
                        json.dump(dj, f, ensure_ascii=False, indent=2)
                    channels.append(li)
            elif args.url:
                slug = args.url.rstrip('/').split('/')[-1]
                fake_movie = {"slug": slug, "title": slug, "thumb": "", "badge": ""}
                res = scrape_movie(page, fake_movie, 1, 1, 
                                 max_episodes=args.max_episodes, 
                                 force_all_episodes=args.all_episodes)
                if res:
                    li, dj = res
                    with open(detail_dir / f"{slug}.json", "w", encoding="utf-8") as f: 
                        json.dump(dj, f, ensure_ascii=False, indent=2)
                    channels.append(li)
            elif args.list_all:
                movies = list_all_movies(page)
                process_movie_list(movies)
            else:
                movies = get_trending_movies(page, limit=max(50, args.max_movies))
                logger.info(f"Found {len(movies)} trending movies. Processing {min(len(movies), args.max_movies)}...")
                process_movie_list(movies)
                
        finally:
            try:
                context.close()
            except:
                pass
            browser.close()

    list_output = {
        "id": "yanhh3d-thuyet-minh",
        "name": "YanHH3D - Thuyet Minh",
        "url": f"{CONFIG['RAW_BASE']}/ophim",
        "search": True,
        "enable_search": True,
        "features": {"search": True},
        "color": "#004444",
        "image": {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyet minh chat luong cao tu YanHH3D.bz",
        "grid_number": 3,
        "channels": channels,
        "sorts": [{"text": "Moi nhat", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}],
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "5.0"
        }
    }
    
    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)
        
    logger.info(f"✅ Done! Saved {list_path} + {len(channels)} detail files.")


if __name__ == "__main__":
    main()
