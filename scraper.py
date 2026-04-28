#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
Lấy 10 phim trending + crawl link .m3u8 (FB CDN/Dailymotion) cho từng tập.
"""

import json
import logging
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "OUTPUT_FILE": "ophim.json",
    "MAX_MOVIES": 10,
    "HOMEPAGE_TIMEOUT": 25000,
    "DETAIL_TIMEOUT": 12000,
    "PLAYER_WAIT": 2500,  # Thời gian chờ player load JS
}

def get_episode_list(page):
    """Trích xuất danh sách tập từ trang chi tiết"""
    try:
        return page.evaluate("""() => {
            const eps = [];
            const selectors = [
                '.server-list .item a', '.epis-list a', '.list-ep a',
                'a[href*="/tap"]', 'a[href*="/tap-"]', '.episode-item a'
            ];
            let links = [];
            for (const s of selectors) {
                const el = document.querySelectorAll(s);
                if (el.length > 0) { links = [...el]; break; }
            }
            // Fallback: tìm tất cả link có chữ 'tập' hoặc số
            if (links.length === 0) {
                links = [...document.querySelectorAll('a')].filter(a => /tap|tập|episode|\\d+/i.test(a.href));
            }
            const seen = new Set();
            links.forEach(a => {
                const href = a.href;
                const text = (a.innerText.trim() || a.title || '').replace(/\\s+/g, ' ');
                if (href && text && !seen.has(href)) {
                    seen.add(href);
                    eps.push({ name: text, url: href });
                }
            });
            // Sort tự nhiên theo số tập
            return eps.sort((a, b) => {
                const na = parseInt(a.name.match(/\\d+/)?.[0] || 0);
                const nb = parseInt(b.name.match(/\\d+/)?.[0] || 0);
                return na - nb;
            });
        }""")
    except Exception as e:
        logger.warning(f"Lỗi lấy danh sách tập: {e}")
        return []

def get_stream_url(detail_page, episode_url):
    """Crawl trang tập phim để lấy link .m3u8 hoặc Dailymotion"""
    collected = []
    def on_response(response):
        if response.status == 200 and ".m3u8" in response.url:
            collected.append(response.url)

    listener = detail_page.on("response", on_response)
    try:
        # Chặn tài nguyên thừa để load nhanh hơn
        detail_page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "font"] else route.continue_())
        
        detail_page.goto(episode_url, wait_until="domcontentloaded", timeout=CONFIG["DETAIL_TIMEOUT"])
        detail_page.wait_for_timeout(CONFIG["PLAYER_WAIT"])

        # Nếu không bắt được từ network, thử lấy trực tiếp từ DOM
        if not collected:
            video_src = detail_page.locator("video").first.get_attribute("src")
            if video_src: collected.append(video_src)
            source_src = detail_page.locator("video source").first.get_attribute("src")
            if source_src: collected.append(source_src)
            
            # Kiểm tra Dailymotion iframe
            iframe = detail_page.locator("iframe[src*='dailymotion']").first
            if iframe.count() > 0:
                collected.append(iframe.get_attribute("src"))
                
    except Exception as e:
        logger.warning(f"Không lấy được stream cho {episode_url}: {e}")
    finally:
        listener.remove_handle()
        detail_page.route("**/*", lambda route: route.continue_())  # Restore
        
    # Trả về link hợp lệ đầu tiên (ưu tiên m3u8, fallback embed)
    return next((u for u in collected if u), None)

def scrape():
    logger.info(f"▶️ Bắt đầu scrape: {CONFIG['BASE_URL']}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        home_page = context.new_page()
        detail_page = context.new_page()

        # Stealth setup
        for pg in [home_page, detail_page]:
            pg.set_extra_http_headers({"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8", "Referer": "https://www.google.com/"})
            pg.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        try:
            # 1️⃣ Lấy trending từ homepage
            home_page.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["HOMEPAGE_TIMEOUT"])
            home_page.wait_for_selector(".flw-item.swiper-slide", state="attached", timeout=10000)
            home_page.wait_for_timeout(1000)

            movies = home_page.evaluate(f"""() => {{
                const res = [], seen = new Set();
                document.querySelectorAll('.flw-item.swiper-slide').forEach(card => {{
                    if (res.length >= {CONFIG["MAX_MOVIES"]}) return;
                    const a = card.querySelector('a.film-poster-ahref');
                    if (!a?.href) return;
                    const slug = a.href.split('/').pop().replace(/\\/$/, '');
                    if (seen.has(slug)) return;
                    seen.add(slug);
                    const title = card.querySelector('.tick.ltr h4, .film-name')?.innerText.trim() || a.title || '';
                    if (!title) return;
                    let thumb = card.querySelector('img[data-src], img.film-poster-img')?.dataset.src || '';
                    if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                    const badge = card.querySelector('.tick.tick-rate, .badge')?.innerText.trim() || '';
                    res.push({{ slug, title, thumb, badge }});
                }});
                return res;
            }}""")
            logger.info(f"✅ Tìm thấy {len(movies)} phim trending")

            # 2️⃣ Crawl chi tiết từng phim
            channels = []
            for i, m in enumerate(movies):
                logger.info(f"🔍 Xử lý: {m['title']} ({i+1}/{len(movies)})")
                detail_page.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=15000)
                detail_page.wait_for_timeout(1500)

                episodes = get_episode_list(detail_page)
                ep_data = []
                for ep in episodes:
                    stream = get_stream_url(detail_page, ep["url"])
                    detail_page.wait_for_timeout(600)  # Giảm tải
                    if stream:
                        ep_data.append({"name": ep["name"], "streams": [{"url": stream}]})
                        logger.debug(f"  ✅ {ep['name']}: {stream[:60]}...")
                    else:
                        logger.warning(f"  ⚠️ {ep['name']}: Không lấy được stream")

                channels.append({
                    "id": m["slug"],
                    "name": m["title"],
                    "description": "",
                    "image": {"url": m["thumb"], "type": "cover", "width": 480, "height": 640},
                    "type": "playlist",
                    "display": "text-below",
                    "label": {"text": m["badge"] or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
                    "remote_data": {"url": f"{CONFIG['BASE_URL']}/{m['slug']}"},
                    "enable_detail": True,
                    "total_episodes": len(ep_data),
                    "episodes": ep_data
                })

        except Exception as e:
            logger.error(f"❌ Lỗi tổng: {e}", exc_info=True)
        finally:
            browser.close()

    # 3️⃣ Xuất JSON
    output = {
        "grid_number": 3,
        "channels": channels,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "2.1"
        }
    }
    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 Đã lưu {len(channels)} phim ({sum(c['total_episodes'] for c in channels)} tập) vào {CONFIG['OUTPUT_FILE']}")
    return output

if __name__ == "__main__":
    scrape()
