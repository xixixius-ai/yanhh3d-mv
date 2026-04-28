#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
- Lấy TOÀN BỘ tập (không giới hạn)
- Tối ưu crawl nhanh: chặn resource thừa, wait ngắn
- Output: mỗi phim có đầy đủ số tập để app hiển thị nút chọn
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
    "MAX_EPISODES_PER_MOVIE": 9999,  # ✅ Lấy toàn bộ tập (không giới hạn)
    "HOMEPAGE_TIMEOUT": 25000,
    "DETAIL_TIMEOUT": 10000,
    "EPISODE_TIMEOUT": 8000,
    "PLAYER_WAIT": 1500,  # ✅ Giảm xuống để nhanh hơn
}

def get_servers_and_episodes(page):
    """Lấy danh sách episodes từ trang chi tiết phim"""
    try:
        return page.evaluate("""() => {
            const result = {};
            const serverTabs = document.querySelectorAll('.server-list .item, .list-server a, .os-server li');
            const servers = [];
            
            if (serverTabs.length > 0) {
                serverTabs.forEach(tab => {
                    const name = (tab.innerText || tab.getAttribute('title') || '').trim();
                    if (name && !/tập|episode|\\d+/i.test(name)) {
                        servers.push({ name, el: tab });
                    }
                });
            } else {
                servers.push({ name: 'Default', el: document.body });
            }
            
            for (const server of servers) {
                const episodes = [];
                const container = server.el.closest('.server-content, .tab-content') || document;
                
                const epSelectors = [
                    '.epis_list a', '.episode-list a', '.list-ep a', 
                    '.episodes a', '.episode-item a', 'a.ep-item',
                    'a[href*="/tap-"]', 'a[href*="/tap/"]'
                ];
                
                let epLinks = [];
                for (const sel of epSelectors) {
                    const found = container.querySelectorAll(sel);
                    if (found.length > 0) { epLinks = [...found]; break; }
                }
                
                if (epLinks.length === 0) {
                    epLinks = [...(container.querySelectorAll('a') || [])].filter(a => {
                        const text = (a.innerText || '').trim();
                        const href = a.href || '';
                        return /tập\\s*\\d+|tap\\s*\\d+/i.test(text) || /\\/tap-?\\d+/i.test(href);
                    });
                }
                
                const seen = new Set();
                epLinks.forEach(a => {
                    const href = a.href;
                    const text = (a.innerText.trim() || a.title || '').replace(/\\s+/g, ' ');
                    if (!href || !text || seen.has(href)) return;
                    if (!/tập|tap|\\d+/i.test(text)) return;
                    seen.add(href);
                    episodes.push({ name: text, url: href });
                });
                
                episodes.sort((a, b) => {
                    const na = parseInt(a.name.match(/\\d+/)?.[0] || 0);
                    const nb = parseInt(b.name.match(/\\d+/)?.[0] || 0);
                    return na - nb;
                });
                
                if (episodes.length > 0) {
                    result[server.name] = episodes;
                }
            }
            
            return result;
        }""")
    except Exception as e:
        logger.warning(f"Lỗi lấy danh sách tập: {e}")
        return {}

def get_stream_url(detail_page, episode_url):
    """Crawl trang tập để lấy link .m3u8"""
    collected = []
    
    def on_response(response):
        if response.status == 200 and ".m3u8" in response.url and "fbcdn" in response.url:
            collected.append(response.url)
    
    detail_page.on("response", on_response)
    
    try:
        def block_unnecessary(route):
            if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
                route.abort()
            else:
                route.continue_()
        detail_page.route("**/*", block_unnecessary)
        
        detail_page.goto(episode_url, wait_until="domcontentloaded", timeout=CONFIG["EPISODE_TIMEOUT"])
        detail_page.wait_for_timeout(CONFIG["PLAYER_WAIT"])

        if not collected:
            for selector in ["video[src]", "video source[src]", "iframe[src*='dailymotion']"]:
                try:
                    el = detail_page.locator(selector).first
                    if el.count() > 0:
                        src = el.get_attribute("src")
                        if src and (".m3u8" in src or "dailymotion" in src):
                            collected.append(src)
                            break
                except: pass
                
    except Exception as e:
        logger.warning(f"Không lấy được stream cho {episode_url}: {e}")
    finally:
        detail_page.remove_listener("response", on_response)
        detail_page.route("**/*", lambda route: route.continue_())
        
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

        for pg in [home_page, detail_page]:
            pg.set_extra_http_headers({"Accept-Language": "vi-VN,vi;q=0.9", "Referer": "https://www.google.com/"})
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
                try:
                    detail_page.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=15000)
                    detail_page.wait_for_timeout(1500)

                    servers_eps = get_servers_and_episodes(detail_page)
                    
                    all_episodes = []
                    for server_name, episodes in servers_eps.items():
                        all_episodes.extend(episodes)
                        break  # Chỉ lấy server đầu tiên
                    
                    # ✅ Crawl stream cho TOÀN BỘ tập (không giới hạn)
                    ep_data = []
                    total_eps = min(len(all_episodes), CONFIG["MAX_EPISODES_PER_MOVIE"])
                    
                    logger.info(f"  📦 {m['title']}: Sẽ crawl {total_eps} tập...")
                    
                    for idx, ep in enumerate(all_episodes[:total_eps]):
                        try:
                            stream = get_stream_url(detail_page, ep["url"])
                            if stream:
                                ep_data.append({
                                    "name": ep["name"],
                                    "streams": [{"url": stream}]
                                })
                                if (idx + 1) % 10 == 0:
                                    logger.info(f"    Progress: {idx + 1}/{total_eps} tập đã crawl")
                            else:
                                logger.warning(f"  ⚠️ {ep['name']}: Không lấy được stream")
                        except Exception as e:
                            logger.warning(f"  ❌ Lỗi tập {ep['name']}: {e}")
                        detail_page.wait_for_timeout(300)  # ✅ Giảm delay để nhanh hơn

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
                    logger.info(f"  ✅ {m['title']}: Hoàn thành {len(ep_data)} tập")

                except Exception as e:
                    logger.error(f"❌ Lỗi phim {m['title']}: {e}")
                    continue

        except Exception as e:
            logger.error(f"❌ Lỗi tổng: {e}", exc_info=True)
        finally:
            browser.close()

    output = {
        "grid_number": 3,
        "channels": channels,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "total_episodes": sum(c["total_episodes"] for c in channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "3.1"
        }
    }
    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 Đã lưu {len(channels)} phim ({output['meta']['total_episodes']} tập) vào {CONFIG['OUTPUT_FILE']}")
    return output

if __name__ == "__main__":
    scrape()
