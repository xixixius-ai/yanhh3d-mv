#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (Production Version)
DEBUG VERSION - in ra title/url/html khi timeout để chẩn đoán CF block
"""

import json
import logging
import os
import random
import time
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
    "MAX_MOVIES":   3,
    "MAX_EPISODES": 3,
    "TIMEOUT_NAV":  30000,
    "TIMEOUT_WAIT": 20000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main")
}

QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd"]

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
    """In ra title + url + 800 ký tự đầu HTML để chẩn đoán CF block"""
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
    """Chờ CF challenge tự resolve rồi mới chờ selector thật"""
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
# FIX: extract thêm latestEpUrl từ poster link (.film-poster-ahref)
# Poster link trỏ thẳng đến tập mới nhất (vd: /tien-nghich/tap-138)
# Title link trỏ đến detail page (vd: /tien-nghich) → KHÔNG có episode list
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

                const posterLink = item.querySelector('.film-poster-ahref');
                const titleLink  = item.querySelector('.film-detail h3 a');

                if (!titleLink?.href) continue;

                const slug  = titleLink.href.split('/').filter(Boolean).pop();
                const title = (titleLink.innerText || titleLink.title || '').trim();
                if (!title || slug.includes('search')) continue;

                let thumb = item.querySelector('img[data-src], img.film-poster-img')?.dataset.src || '';
                if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;

                const badge = item.querySelector('.tick.tick-rate, .fdi-item')?.innerText.trim() || '';

                // Poster link trỏ thẳng đến tập mới nhất (/slug/tap-X)
                const latestEpUrl = posterLink?.href || null;

                results.push({ slug, title, thumb, badge, latestEpUrl });
            }
            return results;
        }""")
        return movies
    except Exception as e:
        logger.error(f"Failed to get trending movies: {e}")
        _debug_page(page, "homepage-error")
        return []


# ── Step 2: Episode page → episode list ──────────────────────────────────────
# FIX: dùng latestEpUrl lấy từ homepage, không hardcode tap-1 hay ghé detail page
# latestEpUrl là URL tập thật (vd: /tien-nghich/tap-138) → có #episodes-content
def get_episodes(page, slug, latest_ep_url):
    if not latest_ep_url:
        logger.warning(f"   No latestEpUrl for {slug}, skipping")
        return []

    try:
        _human_delay(400, 800)
        page.goto(latest_ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
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
                    results.push({ name: text, url: href });
                }
            }
            return results.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")

        if episodes:
            logger.info(f"   Got {len(episodes)} episodes from {latest_ep_url}")
        else:
            logger.warning(f"   No episodes found at {latest_ep_url}")
            _debug_page(page, f"no-ep-list-{slug}")

        return episodes

    except PlaywrightTimeout:
        logger.warning(f"   Timeout at {latest_ep_url}")
        _debug_page(page, f"ep-timeout-{slug}")
        return []
    except Exception as e:
        logger.warning(f"   Error at {latest_ep_url}: {e}")
        _debug_page(page, f"ep-error-{slug}")
        return []


# ── Step 3: Episode page → stream URLs ───────────────────────────────────────
def get_stream_url(page, ep_url):
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

                if (src.includes('fbcdn') && src.includes('.m3u8')) {
                    results.push({ url: src, type: 'hls', label: label });
                } else if (
                    (src.includes('.m3u8') || src.includes('.mp4')) &&
                    !src.includes('play-fb-v8') &&
                    !src.includes('short.icu')
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
        _debug_page(page, "stream-error")
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
    logger.info(f"playwright-stealth: {'OK' if HAS_STEALTH else 'NOT FOUND - using fallback'}")

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
            movies = get_trending_movies(page)
            if not movies:
                logger.error("No movies found. Exiting.")
                return

            limit = min(len(movies), CONFIG["MAX_MOVIES"])
            logger.info(f"Found {len(movies)} movies. Processing {limit}...")

            for idx, movie in enumerate(movies[:limit], 1):
                logger.info(f"[{idx}/{limit}] {movie['title']} ({movie['slug']}) | latestEpUrl={movie.get('latestEpUrl')}")
                try:
                    # FIX: truyền latestEpUrl thay vì để get_episodes tự đoán tap-1
                    episodes = get_episodes(page, movie["slug"], movie.get("latestEpUrl"))
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
                    continue

        except Exception as e:
            logger.error(f"Critical error: {e}")
        finally:
            browser.close()

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
