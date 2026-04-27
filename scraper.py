"""scraper.py — YanHH3D → MonPlayer | 20 phim mới nhất | Minimal"""
import json, re, time, logging
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "https://yanhh3d.bz"
URL = f"{BASE}/moi-cap-nhat"
OUT = "monplayer.json"
MAX = 20

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def get_stream(html):
    m = re.search(r'(https?://[^\s\'"<>]+?\.(m3u8|mp4)[^\s\'"<>]*)', html)
    return m.group(1) if m and 'ads' not in m.group(1).lower() else None

def main():
    items = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        c = b.new_context(viewport={"width": 1920, "height": 1080})
        pg = c.new_page()
        
        print("📥 Loading listing...")
        pg.goto(URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)
        
        # Lấy 20 phim mới nhất
        for a in pg.query_selector_all(".film-poster-ahref[href]")[:MAX]:
            href = a.get_attribute("href")
            title = a.get_attribute("title") or href.strip("/").replace("-", " ").title()
            if not href or "/" in href.strip("/").split("/")[-1]: continue
            
            # Crawl chi tiết phim
            pg.goto(BASE + href, wait_until="networkidle", timeout=30000)
            time.sleep(1)
            
            # Ảnh poster
            poster = ""
            try: poster = pg.locator("meta[property='og:image']").first.get_attribute("content")
            except: pass
            if not poster:
                try: poster = pg.locator("img.film-poster-img").first.get_attribute("data-src") or pg.locator("img.film-poster-img").first.get_attribute("src")
                except: pass
            if poster and poster.startswith("/"): poster = BASE + poster
            
            # Danh sách tập + stream
            streams = []
            for ep in pg.query_selector_all("a[href*='/tap-']"):
                ep_href = ep.get_attribute("href")
                if "/sever" in ep_href.lower(): continue
                ep_url = BASE + ep_href if ep_href.startswith("/") else ep_href
                
                pg.goto(ep_url, wait_until="networkidle", timeout=20000)
                time.sleep(0.3)
                
                stream = get_stream(pg.content())
                if stream:
                    label = ep.text_content().strip() or f"Tập {ep_href.split('/tap-')[-1]}"
                    streams.append({"name": label, "url": stream})
            
            if streams:
                items.append({
                    "title": title,
                    "poster": poster or f"{BASE}/favicon.ico",
                    "image": poster or f"{BASE}/favicon.ico",
                    "streams": streams
                })
                logging.info(f"✓ {title[:40]} — {len(streams)} tập")
            
            if len(items) >= MAX: break
        
        b.close()
    
    # Output JSON
    json.dump({"name": "YanHH3D — 20 Phim Mới Nhất", "items": items}, 
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    logging.info(f"✅ Done: {len(items)} phim → {OUT}")

if __name__ == "__main__":
    main()
