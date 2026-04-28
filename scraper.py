#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (Production Version)
✅ Crawls homepage → episode page (tap-1) → episode list → stream URLs
✅ #episodes-content chỉ có trên episode page, KHÔNG có trên movie detail page
✅ Lấy episode list từ tab #top-comment (Thuyết Minh), bỏ sever2 (Vietsub)
✅ Extracts ALL quality streams: 1080, 4K, 4K-, 1080- từ fbcdn.cloud .m3u8
✅ Multi-quality stream_links trong MonPlayer schema
✅ Robust error handling, logging, retry logic
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────
CONFIG = {
    "BASE_URL":     "https://yanhh3d.bz",
    "OUTPUT_DIR":   "ophim",
    "LIST_FILE":    "ophim.json",
    "MAX_MOVIES":   5,
    "MAX_EPISODES": 2,
    "TIMEOUT_NAV":  20000,
    "TIMEOUT_WAIT": 15000,
    "USER_AGENT":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE":     os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main")
}

# Thứ tự ưu tiên hiển thị (label viết thường)
QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd"]


# ─── Step 1: Homepage → movie list ──────────────────────────────────────────
def get_trending_movies(page):
    """Extract trending movies from homepage"""
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        page.wait_for_selector(".flw-item", state="attached", timeout=CONFIG["TIMEOUT_WAIT"])

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
        return []


# ─── Step 2: Episode page → episode list ────────────────────────────────────
def get_episodes(page, slug):
    """
    Lấy danh sách tập từ episode page (tap-1 hoặc tap tồn tại đầu tiên).

    Cấu trúc thực tế:
    - #episodes-content chỉ có trên /slug/tap-N, KHÔNG có trên /slug
    - Tab #top-comment = Thuyết Minh  -> URL dạng BASE_URL/slug/tap-N
    - Tab #new-comment = Vietsub      -> URL dạng BASE_URL/sever2/slug/tap-N
    - Chỉ lấy tab Thuyết Minh (#top-comment), bỏ sever2
    """
    for tap_num in [1, 2, 3]:
        ep_url = f"{CONFIG['BASE_URL']}/{slug}/tap-{tap_num}"
        try:
            page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
            page.wait_for_selector("#episodes-content", state="attached", timeout=CONFIG["TIMEOUT_WAIT"])

            episodes = page.evaluate("""() => {
                const results = [];

                // Chỉ lấy tab Thuyết Minh: div#top-comment
                // Bỏ div#new-comment (Vietsub - sever2)
                const pane = document.querySelector('#top-comment');
                if (!pane) return results;

                const items = pane.querySelectorAll('a.ssl-item.ep-item');
                for (const item of items) {
                    const href = item.href || '';
                    const text = (
                        item.querySelector('.ssli-order')?.innerText ||
                        item.querySelector('.ep-name')?.innerText ||
                        item.title ||
                        ''
                    ).trim();

                    // Bỏ sever2 (Vietsub)
                    if (href.includes('/sever2/')) continue;

                    // Chỉ lấy tập số nguyên (bỏ "139 TL", "1-5", v.v.)
                    if (href && /^\\d+$/.test(text)) {
                        results.push({ name: text, url: href });
                    }
                }

                // Sort tăng dần theo số tập
                return results.sort((a, b) => parseInt(a.name) - parseInt(b.name));
            }""")

            if episodes:
                logger.info(f"   📋 Lấy episode list từ {ep_url}")
                return episodes

        except PlaywrightTimeout:
            logger.warning(f"   ⏱️ Timeout khi vào {ep_url}, thử tập tiếp...")
            continue
        except Exception as e:
            logger.warning(f"   ⚠️ Lỗi khi vào {ep_url}: {e}")
            continue

    logger.error(f"Không lấy được episode list cho {slug}")
    return []


# ─── Step 3: Episode page → stream URLs ─────────────────────────────────────
def get_stream_url(page, ep_url):
    """
    Thu thập TẤT CA stream links từ #list_sv a.btn3dsv

    Cấu trúc thực tế:
    - LINK1: fbcdn.cloud .m3u8 - label "1080"    OK
    - LINK2: yanhh3d.bz/play-fb-v8/play/ID       SKIP proxy
    - LINK4: fbcdn.cloud .m3u8 - label "1080-"   OK
    - LINK5: fbcdn.cloud .m3u8 - label "4K"      OK
    - LINK6: fbcdn.cloud .m3u8 - label "4K-"     OK
    - LINK9: short.icu/...     - label "Link10"  SKIP shortlink

    Trả về list[{url, type, label}] hoặc None
    """
    try:
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        page.wait_for_selector("#list_sv", state="attached", timeout=CONFIG["TIMEOUT_WAIT"])

        streams = page.evaluate("""() => {
            const results = [];
            const btns = document.querySelectorAll('#list_sv a.btn3dsv');
            for (const btn of btns) {
                const src   = (btn.getAttribute('data-src') || '').trim();
                const label = (btn.innerText || btn.textContent || '').trim();

                // OK: fbcdn.cloud .m3u8 (1080, 4K, 4K-, 1080-)
                if (src.includes('fbcdn') && src.includes('.m3u8')) {
                    results.push({ url: src, type: 'hls', label: label });
                }
                // Fallback: .m3u8 / .mp4 tu domain khac
                else if (
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
                // SKIP: play-fb-v8/play/ va short.icu
            }
            return results;
        }""")

        if streams and len(streams) > 0:
            return streams

        return None

    except Exception as e:
        logger.debug(f"Stream extraction failed for {ep_url}: {e}")
        return None


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _sort_streams(stream_list):
    """Sort theo thu tu uu tien chat luong: 1080 → 4K → 4K- → 1080-"""
    def priority(s):
        lbl = (s.get("label") or "").strip().lower()
        try:
            return QUALITY_PRIORITY.index(lbl)
        except ValueError:
            return 99
    return sorted(stream_list, key=priority)


def build_detail_json(slug, episodes):
    """Build MonPlayer-compatible detail JSON voi multi-quality stream_links"""
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
    """Build MonPlayer-compatible list item"""
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


# ─── Main ────────────────────────────────────────────────────────────────────
def scrape():
    """Main scraper orchestrator"""
    logger.info("Starting YanHH3D to MonPlayer scraper...")
    channels   = []
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=CONFIG["USER_AGENT"],
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()

        try:
            # 1) Homepage
            movies = get_trending_movies(page)
            if not movies:
                logger.error("No movies found. Exiting.")
                return

            limit = min(len(movies), CONFIG["MAX_MOVIES"])
            logger.info(f"Found {len(movies)} movies. Processing {limit}...")

            # 2) Xu ly tung phim
            for idx, movie in enumerate(movies[:limit], 1):
                logger.info(f"[{idx}/{limit}] {movie['title']} ({movie['slug']})")
                try:
                    # Goto tap-1 de lay episode list
                    # KHONG goto movie detail page vi khong co #episodes-content
                    episodes = get_episodes(page, movie["slug"])
                    if not episodes:
                        logger.warning(f"No episodes found for {movie['slug']}")
                        continue

                    logger.info(f"   Found {len(episodes)} episodes. Extracting streams...")

                    # 3) Lay stream tung tap
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
                            logger.warning(f"      Tap {ep['name']}: khong tim thay stream")

                        if (i + 1) % 10 == 0:
                            logger.info(f"   Progress: {i + 1}/{crawl_limit} processed")

                        page.wait_for_timeout(150)

                    # 4) Luu detail JSON
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
            logger.error(f"Critical scraper error: {e}")
        finally:
            browser.close()

    # 5) Luu list JSON
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

    logger.info(f"Scraper finished! Saved {list_path} + {len(channels)} detail files.")
    return list_output


if __name__ == "__main__":
    scrape()
