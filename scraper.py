#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v1.3)
Chiến lược stream URL:
  1. short.icu → abyss.to embed → Playwright extract m3u8/mp4 thật (ưu tiên #1)
  2. fbcdn.cloud m3u8 → giữ nguyên làm fallback (Mon player tự xử)
  3. Bỏ hoàn toàn: play-fb-v8, ffmpeg merge, proxy
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
}

QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd", "abyss", "link10"]

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


# ── short.icu resolver ────────────────────────────────────────────────────────
def resolve_short_icu(short_url: str) -> str | None:
    """Follow redirect short.icu → trả về URL đích (thường là abyss.to)"""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        req    = urllib.request.Request(short_url, headers=SHORT_ICU_HEADERS, method="GET")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.url
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                location = e.headers.get("Location", "")
                if location:
                    logger.info(f"   short.icu {e.code} → {location}")
                    if "short.icu" in location:
                        return resolve_short_icu(location)
                    return location
            raise
    except Exception as e:
        logger.warning(f"   resolve_short_icu failed for {short_url}: {e}")
        return None


# ── Abyss.to deep extractor ───────────────────────────────────────────────────
def extract_abyss_stream(context, abyss_url: str) -> dict | None:
    """
    Playwright mở embed page abyss.to, bắt network request để lấy
    URL m3u8/mp4 thật mà player gọi tới CDN.
    Trả về {"url": ..., "type": "hls"|"mp4"} hoặc None.
    """
    found = {"url": None, "type": None}

    def _on_request(request):
        url = request.url
        if found["url"]:
            return
        # Bắt m3u8 từ CDN (không phải từ abyss domain chính)
        if ".m3u8" in url and "abyss.to" not in url:
            found["url"]  = url
            found["type"] = "hls"
        elif re.search(r"\.(mp4|webm)(\?|$)", url.split("?")[0]):
            if any(x in url for x in ["cdn", "storage", "video", "media", "stream", "content"]):
                found["url"]  = url
                found["type"] = "mp4"

    page = context.new_page()
    try:
        page.on("request", _on_request)
        logger.info(f"      Playwright → abyss: {abyss_url}")
        page.goto(abyss_url, wait_until="domcontentloaded", timeout=20000)

        # Chờ tối đa 15s để player init và fire request stream
        for _ in range(30):
            if found["url"]:
                break
            time.sleep(0.5)

        # Fallback: parse HTML tìm source trong jwplayer config / <source> tag
        if not found["url"]:
            html = page.content()
            patterns = [
                (r"""['"]file['"]\s*:\s*['"]([^'"]+\.m3u8[^'"]*)['"]""", "hls"),
                (r"""['"]file['"]\s*:\s*['"]([^'"]+\.mp4[^'"]*)['"]""",  "mp4"),
                (r"""<source[^>]+src=['"]([^'"]+\.m3u8[^'"]*)['"]""",    "hls"),
                (r"""<source[^>]+src=['"]([^'"]+\.mp4[^'"]*)['"]""",     "mp4"),
                (r"""source\s*:\s*['"]([^'"]+\.m3u8[^'"]*)['"]""",       "hls"),
            ]
            for pattern, vtype in patterns:
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    found["url"]  = m.group(1)
                    found["type"] = vtype
                    break

        if found["url"]:
            logger.info(f"      ✓ Abyss stream [{found['type']}]: {found['url'][:80]}...")
        else:
            logger.warning("      ✗ Abyss: không extract được stream URL")
            _debug_page(page, "abyss-fail")

        return found if found["url"] else None

    except Exception as e:
        logger.warning(f"      Abyss extract error: {e}")
        return None
    finally:
        page.close()


# ── Step 1: Homepage → movie list ─────────────────────────────────────────────
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


# ── Step 2: Episode list ───────────────────────────────────────────────────────
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
        return []
    except Exception as e:
        logger.warning(f"   Error at {latest_url}: {e}")
        return []


# ── Step 3: Stream extraction ──────────────────────────────────────────────────
def get_stream_url(page, context, ep_url):
    """
    Trả về list stream:
      - abyss  → Playwright extract m3u8/mp4 thật  (type: hls/mp4)
      - fbcdn  → giữ nguyên m3u8 URL               (type: hls, fallback)
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

            # ── short.icu → abyss → Playwright extract stream thật ────────
            if "short.icu" in src:
                logger.info(f"      Resolving short.icu ({label}): {src}")
                resolved = resolve_short_icu(src)
                if not resolved:
                    logger.warning("      short.icu resolve failed")
                    continue

                if "abyss.to" in resolved:
                    abyss_embed = re.sub(r"abyss\.to/v/", "abyss.to/e/", resolved)
                    if not abyss_embed.startswith("http"):
                        abyss_embed = "https://" + abyss_embed

                    abyss_stream = extract_abyss_stream(context, abyss_embed)
                    if abyss_stream:
                        streams.append({
                            "url":   abyss_stream["url"],
                            "type":  abyss_stream["type"],
                            "label": f"abyss-{label}" if label else "abyss",
                        })
                    else:
                        # Fallback: vẫn giữ embed URL
                        streams.append({
                            "url":   abyss_embed,
                            "type":  "embed",
                            "label": "abyss-embed",
                        })
                else:
                    vtype = "hls" if ".m3u8" in resolved else "mp4" if ".mp4" in resolved else "embed"
                    streams.append({"url": resolved, "type": vtype, "label": label or "ext"})
                continue

            # ── fbcdn.cloud m3u8 → giữ nguyên làm fallback ───────────────
            if "fbcdn" in src and ".m3u8" in src:
                streams.append({"url": src, "type": "hls", "label": label or "fb-hls"})
                continue

            # ── Bỏ qua play-fb-v8 ─────────────────────────────────────────
            if "play-fb-v8" in src:
                logger.debug("      Skip play-fb-v8")
                continue

            # ── Nguồn khác ────────────────────────────────────────────────
            if ".m3u8" in src:
                streams.append({"url": src, "type": "hls", "label": label})
            elif ".mp4" in src:
                streams.append({"url": src, "type": "mp4", "label": label})

        if streams:
            summary = [(s["type"], s["label"]) for s in streams]
            logger.info(f"      Streams OK: {summary}")

        return streams if streams else None

    except Exception as e:
        logger.debug(f"Stream extraction failed for {ep_url}: {e}")
        _debug_page(page, "stream-error")
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────
def _sort_streams(stream_list):
    """mp4/hls resolved lên đầu, embed xuống cuối, sau đó theo quality label"""
    def priority(s):
        type_rank = {"mp4": 0, "hls": 1, "embed": 2}.get(s.get("type", "embed"), 3)
        lbl = re.sub(r"^abyss-", "", (s.get("label") or "").strip().lower())
        try:
            quality_rank = QUALITY_PRIORITY.index(lbl)
        except ValueError:
            quality_rank = 99
        return (type_rank, quality_rank)
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


# ── Main ───────────────────────────────────────────────────────────────────────
def scrape():
    logger.info("Starting YanHH3D to MonPlayer scraper (v1.3)...")
    logger.info(f"playwright-stealth: {'OK' if HAS_STEALTH else 'NOT FOUND'}")
    logger.info("Stream priority: abyss extract > fbcdn m3u8 fallback")

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
                        stream = get_stream_url(page, context, ep["url"])
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                        else:
                            logger.warning(f"      Tap {ep['name']}: no stream")

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
            "version":     "1.3"
        }
    }

    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)

    logger.info(f"Done! Saved {list_path} + {len(channels)} detail files.")
    return list_output


if __name__ == "__main__":
    scrape()
