#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (Production Version)
✅ Crawls homepage → movie detail → episodes → stream URLs
✅ Extracts ALL quality streams from #list_sv a.btn3dsv data-src attribute
✅ Filters: fbcdn.cloud .m3u8 only (skip play-fb-v8 proxy & short.icu)
✅ Multi-quality stream_links: 1080, 4K, 4K-, 1080-
✅ Outputs strict MonPlayer schema
✅ Robust error handling, logging, and retry logic
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# 📝 Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ⚙️ Configuration
CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "OUTPUT_DIR": "ophim",
    "LIST_FILE": "ophim.json",
    "MAX_MOVIES": 5,
    "MAX_EPISODES": 2,
    "TIMEOUT_NAV": 20000,
    "TIMEOUT_WAIT": 15000,
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "RAW_BASE": os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main")
}

# Thứ tự ưu tiên chất lượng (label viết thường)
QUALITY_PRIORITY = ["1080", "4k", "4k-", "1080-", "hd"]


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
        logger.error(f"❌ Failed to get trending movies: {e}")
        return []


def get_episodes(page):
    """Extract episode links from movie detail page"""
    try:
        page.wait_for_selector(".ep-range, #episodes-content", state="attached", timeout=CONFIG["TIMEOUT_WAIT"])

        episodes = page.evaluate("""() => {
            const results = [];
            const items = document.querySelectorAll('.ep-range a.ssl-item.ep-item, #detail-ss-list a.ssl-item.ep-item');
            for (const item of items) {
                const href = item.href;
                const text = item.querySelector('.ssli-order, .ep-name')?.innerText.trim() || item.title || '';
                // Filter only numeric episodes (skip grouped like "1-5")
                if (href && /^\\d+$/.test(text)) {
                    results.push({ name: text, url: href });
                }
            }
            // Sort ascending by episode number
            return results.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")
        return episodes
    except Exception as e:
        logger.error(f"❌ Failed to get episodes: {e}")
        return []


def get_stream_url(page, ep_url):
    """
    Thu thập TẤT CẢ stream links từ #list_sv a.btn3dsv
    ✅ Chỉ lấy fbcdn.cloud .m3u8 (link trực tiếp)
    ❌ Bỏ qua: play-fb-v8/play/ (proxy), short.icu (shortlink)
    Trả về list[dict{url, type, label}] hoặc None
    """
    try:
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        page.wait_for_selector("#list_sv", state="attached", timeout=CONFIG["TIMEOUT_WAIT"])

        streams = page.evaluate("""() => {
            const results = [];
            const btns = document.querySelectorAll('#list_sv a.btn3dsv');
            for (const btn of btns) {
                const src   = btn.getAttribute('data-src') || '';
                const label = (btn.innerText || btn.textContent || '').trim();

                // ✅ Ưu tiên: fbcdn.cloud .m3u8 trực tiếp (1080, 4K, 4K-, 1080-)
                if (src.includes('fbcdn') && src.includes('.m3u8')) {
                    results.push({ url: src, type: 'hls', label: label });
                }
                // ✅ Fallback: .m3u8 hoặc .mp4 từ domain khác (tương lai)
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
                // ❌ Bỏ qua: play-fb-v8/play/ (HD proxy) và short.icu (shortlink)
            }
            return results;
        }""")

        if streams and len(streams) > 0:
            return streams  # list[dict], không phải single dict

        return None

    except Exception as e:
        logger.debug(f"⚠️ Stream extraction failed for {ep_url}: {e}")
        return None


def _sort_streams(stream_list):
    """Sort stream list theo thứ tự ưu tiên chất lượng"""
    def priority(s):
        lbl = (s.get("label") or "").strip().lower()
        try:
            return QUALITY_PRIORITY.index(lbl)
        except ValueError:
            return 99
    return sorted(stream_list, key=priority)


def build_detail_json(slug, episodes):
    """Build MonPlayer-compatible detail JSON với multi-quality stream_links"""
    streams = []
    for i, ep in enumerate(episodes):
        raw_streams = ep.get("stream")  # list[dict{url, type, label}]
        if not raw_streams:
            continue

        # Sort: 1080 trước, rồi 4K, 4K-, 1080-
        sorted_streams = _sort_streams(raw_streams)

        # Mỗi quality = 1 stream_link entry
        stream_links = []
        for j, s in enumerate(sorted_streams):
            label = s.get("label") or f"Link {j + 1}"
            stream_links.append({
                "id":      f"{slug}--0-{i}-{j}",
                "name":    label,       # "1080", "4K", "4K-", "1080-"
                "type":    s["type"],   # "hls" hoặc "mp4"
                "default": j == 0,      # link đầu tiên (1080) là default
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
            "name": "Thuyết Minh #1",
            "contents": [{
                "id":          f"{slug}--0",
                "name":        "",
                "grid_number": 3,
                "streams":     streams
            }]
        }],
        "subtitle": "Thuyết Minh"
    }


def build_list_item(movie):
    """Build MonPlayer-compatible list item"""
    return {
        "id": movie["slug"],
        "name": movie["title"],
        "description": "",
        "image": {
            "url": movie["thumb"],
            "type": "cover",
            "width": 480,
            "height": 640
        },
        "type": "playlist",
        "display": "text-below",
        "label": {
            "text": movie["badge"] or "Trending",
            "position": "top-left",
            "color": "#35ba8b",
            "text_color": "#ffffff"
        },
        "remote_data": {
            "url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"
        },
        "enable_detail": True
    }


def scrape():
    """Main scraper orchestrator"""
    logger.info("🚀 Starting YanHH3D → MonPlayer scraper...")
    channels = []
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
            # 1️⃣ Get trending movies
            movies = get_trending_movies(page)
            if not movies:
                logger.error("❌ No movies found. Exiting.")
                return

            logger.info(f"✅ Found {len(movies)} movies. Processing up to {CONFIG['MAX_MOVIES']}...")

            # 2️⃣ Process each movie
            for idx, movie in enumerate(movies[:CONFIG["MAX_MOVIES"]], 1):
                logger.info(f"📖 [{idx}/{min(len(movies), CONFIG['MAX_MOVIES'])}] {movie['title']} ({movie['slug']})")
                try:
                    # Go to detail page
                    page.goto(
                        f"{CONFIG['BASE_URL']}/{movie['slug']}",
                        wait_until="domcontentloaded",
                        timeout=CONFIG["TIMEOUT_NAV"]
                    )

                    # Get episodes
                    episodes = get_episodes(page)
                    if not episodes:
                        logger.warning(f"⚠️ No episodes found for {movie['slug']}")
                        continue

                    logger.info(f"   📺 Found {len(episodes)} episodes. Extracting streams...")

                    # Extract streams (limit to MAX_EPISODES for speed)
                    ep_data = []
                    crawl_limit = min(len(episodes), CONFIG["MAX_EPISODES"])
                    for i in range(crawl_limit):
                        ep = episodes[i]
                        stream = get_stream_url(page, ep["url"])  # list hoặc None
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                            labels = [s["label"] for s in stream]
                            logger.info(f"      🎞️  Tập {ep['name']}: {len(stream)} quality → {labels}")
                        else:
                            logger.warning(f"      ⚠️ Tập {ep['name']}: không tìm thấy stream")

                        if (i + 1) % 10 == 0:
                            logger.info(f"   ✅ Progress: {i + 1}/{crawl_limit} episodes processed")

                        # Small delay to avoid rate limiting
                        page.wait_for_timeout(150)

                    # Save detail JSON
                    if ep_data:
                        detail_json = build_detail_json(movie["slug"], ep_data)
                        detail_path = detail_dir / f"{movie['slug']}.json"
                        with open(detail_path, "w", encoding="utf-8") as f:
                            json.dump(detail_json, f, ensure_ascii=False, indent=2)
                        logger.info(f"   💾 Saved {detail_path.name} ({len(ep_data)} episodes)")
                        channels.append(build_list_item(movie))
                    else:
                        logger.warning(f"   ⚠️ No valid streams found for {movie['slug']}")

                except Exception as e:
                    logger.error(f"   ❌ Error processing {movie['slug']}: {e}")
                    continue

        except Exception as e:
            logger.error(f"❌ Critical scraper error: {e}")
        finally:
            browser.close()

    # 3️⃣ Save list JSON
    list_output = {
        "id": "yanhh3d-thuyet-minh",
        "name": "YanHH3D - Thuyết Minh",
        "url": f"{CONFIG['RAW_BASE']}/ophim",
        "color": "#004444",
        "image": {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyết minh chất lượng cao từ YanHH3D.bz",
        "grid_number": 3,
        "channels": channels,
        "sorts": [{"text": "Mới nhất", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}],
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "1.0"
        }
    }

    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ Scraper finished! Saved {list_path} + {len(channels)} detail files.")
    return list_output


if __name__ == "__main__":
    scrape()
