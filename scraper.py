#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (Production Version - FIXED)

FIX ROOT CAUSE:
  - URL pattern /slug/tap-1 KHÔNG tồn tại trên yanhh3d.bz
  - Site dùng số tập thật: /tien-nghich/tap-138
  - Episode list nằm trên MOVIE DETAIL PAGE /slug, không phải episode page

FLOW MỚI:
  1. Homepage → lấy movie slug từ .flw-item
  2. Goto /slug (movie detail page)
  3. Trên movie detail page → lấy episode list từ #top-comment tab
  4. Với mỗi episode → lấy stream URL từ #list_sv
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── playwright-stealth (optional nhưng quan trọng) ───────────────────────────
try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    print("[WARN] playwright-stealth chưa cài. Chạy: pip install playwright-stealth")

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG = {
    "BASE_URL":     "https://yanhh3d.bz",
    "OUTPUT_DIR":   "ophim",
    "LIST_FILE":    "ophim.json",
    "MAX_MOVIES":   5,
    "MAX_EPISODES": 2,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main")
}

QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd"]

EXTRA_HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Sec-Fetch-User":  "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _human_delay(min_ms=300, max_ms=900):
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def _apply_stealth(page):
    if HAS_STEALTH:
        stealth_sync(page)
    else:
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US'] });
            window.chrome = { runtime: {} };
        """)


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


# ── Step 1: Homepage → movie list ────────────────────────────────────────────
def get_trending_movies(page):
    """Extract trending movies from homepage - lấy slug từ film-poster-ahref"""
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])

        movies = page.evaluate("""() => {
            const results = [];
            const items = document.querySelectorAll('.flw-item');
            for (const item of items) {
                if (results.length >= 10) break;
                
                // Lấy link từ film-poster-ahref (movie detail page, không phải episode)
                const posterLink = item.querySelector('.film-poster-ahref');
                const detailLink = item.querySelector('.film-detail h3 a');
                const link = posterLink || detailLink;
                
                if (!link?.href) continue;

                // Extract slug từ URL movie detail: /tien-nghich
                const href = link.href;
                const parts = href.split('/').filter(p => p);
                const slug = parts[parts.length - 1];
                
                const title = link.innerText.trim() || link.title || '';
                if (!title || !slug || slug.includes('search')) continue;

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
        return []


# ── Step 2: Movie detail page → episode list ─────────────────────────────────
def get_episodes(page, slug):
    """
    Lấy danh sách tập từ MOVIE DETAIL PAGE /slug.
    
    FIX: Episode list nằm trên /slug (movie detail), KHÔNG phải /slug/tap-N
    Tab Thuyết Minh = #top-comment → lấy từ đây
    """
    movie_url = f"{CONFIG['BASE_URL']}/{slug}"
    try:
        _human_delay(400, 800)
        page.goto(movie_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        
        # Chờ episode container trên movie detail page
        _wait_for_cf(page, "#episodes-content, #detail-ss-list", CONFIG["TIMEOUT_WAIT"])

        episodes = page.evaluate("""() => {
            const results = [];
            
            // Tìm tab Thuyết Minh (#top-comment) - đây là tab mặc định active
            const pane = document.querySelector('#top-comment');
            if (!pane) return results;

            // Lấy tất cả episode items trong tab Thuyết Minh
            const items = pane.querySelectorAll('a.ssl-item.ep-item');
            for (const item of items) {
                const href = item.href || '';
                
                // Bỏ link sever2 (Vietsub) - chỉ lấy Thuyết Minh
                if (href.includes('/sever2/')) continue;
                
                // Extract episode name/number
                const order = item.querySelector('.ssli-order')?.innerText.trim();
                const name = item.querySelector('.ep-name')?.innerText.trim();
                const text = order || name || item.title || '';
                
                // Chỉ lấy tập có số (bỏ "139 TL", "51-55", v.v. nếu cần lọc thêm)
                if (href && text && /^\\d+$/.test(text)) {
                    results.push({ name: text, url: href });
                }
            }
            
            // Sort by episode number ascending
            return results.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")

        if episodes:
            logger.info(f"   Got episode list from {movie_url} ({len(episodes)} eps)")
            return episodes
        else:
            logger.warning(f"   No episodes found in #top-comment for {slug}")
            return []

    except PlaywrightTimeout:
        logger.error(f"   Timeout loading {movie_url}")
        return []
    except Exception as e:
        logger.error(f"   Error getting episodes for {slug}: {e}")
        return []


# ── Step 3: Episode page → stream URLs ───────────────────────────────────────
def get_stream_url(page, ep_url):
    """
    Thu thập tất cả stream links từ #list_sv a.btn3dsv.
    Chỉ lấy fbcdn.cloud .m3u8 (link trực tiếp).
    Bỏ: play-fb-v8 (proxy), short.icu (shortlink).
    """
    try:
        _human_delay(200, 500)
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#list_sv", CONFIG["TIMEOUT_WAIT"])

        streams = page.evaluate("""() => {
            const results = [];
            const btns = document.querySelectorAll('#list_sv a.btn3dsv');
            for (const btn of btns) {
                const src   = (btn.getAttribute('data-src') || '').trim();
                const label = (btn.innerText || btn.textContent || '').trim();

                // Ưu tiên: fbcdn.cloud .m3u8 (direct HLS)
                if (src.includes('fbcdn') && src.includes('.m3u8')) {
                    results.push({ url: src, type: 'hls', label: label });
                } 
                // Fallback: .m3u8 hoặc .mp4 trực tiếp (không qua proxy/shortlink)
                else if (
                    (src.includes('.m3u8') || src.includes('.mp4')) &&
                    !src.includes('play-fb-v8') &&
                    !src.includes('short.icu') &&
                    src.startsWith('http')
                ) {
                    results.push({
                        url:   src,
                        type:  src.includes('.m3u8') ? 'hls' : 'mp4',
                        label: label
                    });
                }
            }
            return results;
        }""")

        return streams if streams else None

    except Exception as e:
        logger.debug(f"Stream extraction failed for {ep_url}: {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _sort_streams(stream_list):
    def priority(s):
        lbl = (s.get("label") or "").strip().lower()
        try:
            return QUALITY_PRIORITY.index(lbl)
        except ValueError:
            return 99
    return sorted(stream_list, key=priority)


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
                "request_headers": [
                    {"key": "User-Agent", "value": CONFIG["USER_AGENT"]},
                    {"key": "Referer",    "value": CONFIG["BASE_URL"]}
                ]
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
            "text":       movie["badge"] or "Trending",
            "position":   "top-left",
            "color":      "#35ba8b",
            "text_color": "#ffffff"
        },
        "remote_data": {
            "url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"
        },
        "enable_detail": True
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def scrape():
    logger.info("Starting YanHH3D to MonPlayer scraper...")
    if not HAS_STEALTH:
        logger.warning("playwright-stealth not found — CF may block. Install: pip install playwright-stealth")

    channels   = []
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--lang=vi-VN",
            ]
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
            # 1) Homepage → lấy movie list
            movies = get_trending_movies(page)
            if not movies:
                logger.error("No movies found. Exiting.")
                return

            limit = min(len(movies), CONFIG["MAX_MOVIES"])
            logger.info(f"Found {len(movies)} movies. Processing {limit}...")

            # 2) Xử lý từng phim
            for idx, movie in enumerate(movies[:limit], 1):
                logger.info(f"[{idx}/{limit}] {movie['title']} ({movie['slug']})")
                try:
                    # FIX: Lấy episode list từ MOVIE DETAIL PAGE /slug
                    episodes = get_episodes(page, movie["slug"])
                    if not episodes:
                        logger.warning(f"No episodes found for {movie['slug']}")
                        continue

                    logger.info(f"   Found {len(episodes)} episodes. Extracting streams...")

                    ep_data     = []
                    crawl_limit = min(len(episodes), CONFIG["MAX_EPISODES"])

                    for i in range(crawl_limit):
                        ep     = episodes[i]
                        stream = get_stream_url(page, ep["url"])

                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                            labels = [s["label"] for s in stream]
                            logger.info(f"      Tap {ep['name']}: {len(stream)} quality -> {labels}")
                        else:
                            logger.warning(f"      Tap {ep['name']}: no stream found")

                        if (i + 1) % 10 == 0:
                            logger.info(f"   Progress: {i + 1}/{crawl_limit}")

                    if ep_data:
                        detail_json = build_detail_json(movie["slug"], ep_data)
                        detail_path = detail_dir / f"{movie['slug']}.json"
                        with open(detail_path, "w", encoding="utf-8") as f:
                            json.dump(detail_json, f, ensure_ascii=False, indent=2)
                        logger.info(f"   Saved {detail_path.name} ({len(ep_data)} episodes)")
                        channels.append(build_list_item(movie))
                    else:
                        logger.warning(f"   No valid streams for {movie['slug']}")

                except Exception as e:
                    logger.error(f"   Error processing {movie['slug']}: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    continue

        except Exception as e:
            logger.error(f"Critical error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        finally:
            browser.close()

    # 3) Lưu list JSON
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
            "version":     "1.0"
        }
    }

    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)

    logger.info(f"Done! Saved {list_path} + {len(channels)} detail files.")
    return list_output


if __name__ == "__main__":
    scrape()
