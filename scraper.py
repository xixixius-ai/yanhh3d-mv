#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (Production Version)
Chiến lược stream URL:
  1. short.icu → follow redirect → lấy abyss.to embed URL (ưu tiên #1)
  2. fbcdn.cloud m3u8 → dùng trực tiếp làm fallback (Mon player không đọc được thì bỏ qua)
  3. play-fb-v8 → bỏ qua (proxy site chặn)
"""

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
    # Không dùng PROXY_BASE nữa - đã bị chặn
    # Abyss embed là nguồn ưu tiên, fbcdn là fallback
}

# Abyss.to: Mon player đọc được qua embed URL dạng https://abyss.to/e/<id>
# Ưu tiên label theo thứ tự này khi chọn stream tốt nhất
QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd", "link10"]

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

SHORT_ICU_HEADERS = {
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


# ── Abyss.to resolver ─────────────────────────────────────────────────────────
def resolve_short_icu(short_url: str) -> str | None:
    """
    Follow redirect của short.icu để lấy URL đích thực.
    short.icu/xxxxx → redirect → abyss.to/e/<id> hoặc URL khác
    Trả về URL cuối cùng sau redirect, hoặc None nếu thất bại.
    """
    try:
        req = urllib.request.Request(short_url, headers=SHORT_ICU_HEADERS, method="GET")
        # Không follow redirect tự động, bắt thủ công để lấy Location header
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())

        # Tắt auto-redirect để bắt được 301/302
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        no_redirect_opener = urllib.request.build_opener(NoRedirect())
        try:
            with no_redirect_opener.open(req, timeout=10) as resp:
                # Nếu không redirect (200 thẳng)
                final_url = resp.url
                logger.info(f"   short.icu → no redirect → {final_url}")
                return final_url
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location", "")
                if location:
                    logger.info(f"   short.icu → {e.code} → {location}")
                    # Nếu redirect tiếp, follow một lần nữa
                    if "short.icu" in location:
                        return resolve_short_icu(location)
                    return location
            raise
    except Exception as e:
        logger.warning(f"   resolve_short_icu failed for {short_url}: {e}")
        return None


def normalize_abyss_url(url: str) -> str | None:
    """
    Chuẩn hóa abyss.to URL về dạng embed mà Mon player đọc được.
    abyss.to/v/<id>  → abyss.to/e/<id>
    abyss.to/e/<id>  → giữ nguyên
    """
    if not url or "abyss.to" not in url:
        return None
    # Đổi /v/ thành /e/ nếu cần
    url = re.sub(r"abyss\.to/v/", "abyss.to/e/", url)
    # Đảm bảo có https://
    if not url.startswith("http"):
        url = "https://" + url
    return url


# ── Step 1: Homepage → movie list ────────────────────────────────────────────
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


# ── Step 1b: Detail page → latest episode URL ────────────────────────────────
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
            _debug_page(page, f"no-tap-{slug}")

        return latest_url

    except PlaywrightTimeout:
        logger.warning(f"   Timeout on detail page for {slug}")
        _debug_page(page, f"detail-timeout-{slug}")
        return None
    except Exception as e:
        logger.warning(f"   Error on detail page for {slug}: {e}")
        _debug_page(page, f"detail-error-{slug}")
        return None


# ── Step 2: Episode page → episode list ──────────────────────────────────────
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
                    results.push({ name: text, url: href });
                }
            }
            return results.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")

        if episodes:
            logger.info(f"   Got {len(episodes)} episodes from {latest_url}")
        else:
            logger.warning(f"   No episodes found at {latest_url}")
            _debug_page(page, f"no-ep-list-{slug}")

        return episodes

    except PlaywrightTimeout:
        logger.warning(f"   Timeout at {latest_url}")
        _debug_page(page, f"ep-timeout-{slug}")
        return []
    except Exception as e:
        logger.warning(f"   Error at {latest_url}: {e}")
        _debug_page(page, f"ep-error-{slug}")
        return []


# ── Step 3: Episode page → stream URLs ───────────────────────────────────────
def get_stream_url(page, ep_url):
    """
    Trả về list stream, ưu tiên:
      - abyss.to embed (type: embed) — Mon player đọc tốt nhất
      - fbcdn.cloud m3u8 (type: hls)  — fallback nếu không có abyss
    Bỏ qua: play-fb-v8 (proxy bị chặn)
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

        streams = []

        for btn in raw_buttons:
            src   = btn["src"]
            label = btn["label"]

            # ── Abyss.to qua short.icu redirect (ưu tiên #1) ──────────────
            if "short.icu" in src:
                logger.info(f"      Resolving short.icu: {src}")
                resolved = resolve_short_icu(src)
                if resolved:
                    abyss_url = normalize_abyss_url(resolved)
                    if abyss_url:
                        streams.append({
                            "url":   abyss_url,
                            "type":  "embed",
                            "label": label or "abyss",
                        })
                        logger.info(f"      → abyss embed: {abyss_url}")
                    else:
                        # Không phải abyss, nhưng vẫn là embed URL khác
                        streams.append({
                            "url":   resolved,
                            "type":  "embed",
                            "label": label or "link-ext",
                        })
                        logger.info(f"      → embed (non-abyss): {resolved}")
                else:
                    logger.warning(f"      short.icu resolve failed: {src}")
                continue

            # ── fbcdn.cloud HLS (fallback #2) ─────────────────────────────
            if "fbcdn" in src and src.endswith(".m3u8"):
                streams.append({
                    "url":   src,
                    "type":  "hls",
                    "label": label or "fb-hls",
                })
                continue

            # ── Bỏ qua play-fb-v8 (proxy bị chặn) ───────────────────────
            if "play-fb-v8" in src:
                logger.debug(f"      Skipping play-fb-v8: {src}")
                continue

            # ── Các loại khác: m3u8/mp4 từ domain lạ ────────────────────
            if src.endswith(".m3u8"):
                streams.append({"url": src, "type": "hls",  "label": label})
            elif src.endswith(".mp4"):
                streams.append({"url": src, "type": "mp4",  "label": label})

        if streams:
            abyss_count = sum(1 for s in streams if s["type"] == "embed")
            hls_count   = sum(1 for s in streams if s["type"] == "hls")
            logger.info(f"      Streams: {abyss_count} embed + {hls_count} hls")

        return streams if streams else None

    except Exception as e:
        logger.debug(f"Stream extraction failed for {ep_url}: {e}")
        _debug_page(page, "stream-error")
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _sort_streams(stream_list):
    """
    Sắp xếp: embed (abyss) lên trước, sau đó theo quality label.
    """
    def priority(s):
        # Embed lên đầu tiên
        if s.get("type") == "embed":
            lbl = (s.get("label") or "").strip().lower()
            try:
                return (0, QUALITY_PRIORITY.index(lbl))
            except ValueError:
                return (0, 50)
        # HLS sau
        lbl = (s.get("label") or "").strip().lower()
        try:
            return (1, QUALITY_PRIORITY.index(lbl))
        except ValueError:
            return (1, 99)
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
    logger.info("Stream priority: abyss.to embed > fbcdn.cloud HLS")

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
                logger.info(f"[{idx}/{limit}] {movie['title']} ({movie['slug']})")
                try:
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
                            labels = [f"{s['type']}:{s['label']}" for s in stream]
                            logger.info(f"      Tap {ep['name']}: {len(stream)} streams → {labels}")
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
            "version":     "1.1"
        }
    }

    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)

    logger.info(f"Done! Saved {list_path} + {len(channels)} detail files.")
    return list_output


if __name__ == "__main__":
    scrape()
