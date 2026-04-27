#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, logging
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

# Cấu hình tối giản
CONFIG = {
    "base_url": "https://yanhh3d.bz",
    "output_file": os.getenv("OUTPUT_PATH", "yanhh3d.json"),
}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

class YanHH3DScraper:
    def __init__(self):
        self.base = CONFIG["base_url"].rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def get_items(self, path):
        items = []
        try:
            r = self.session.get(urljoin(self.base, path), timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")
            
            # Selector này quét các khối phim phổ biến nhất hiện nay (flw-item)
            cards = soup.select(".flw-item, .film-item, .ml-item")
            
            for card in cards:
                a_tag = card.select_one("a[href*='/phim/']")
                if not a_tag: continue
                
                title = a_tag.get("title") or card.select_one(".film-name").get_text(strip=True)
                href = a_tag.get("href")
                img = card.select_one("img").get("data-src") or card.select_one("img").get("src")
                
                # Lấy tập phim (label)
                ep_label = card.select_one(".tick-eps, .tick-sub, .label")
                label_text = ep_label.get_text(strip=True) if ep_label else "Full"

                items.append({
                    "name": title,
                    "slug": href.split('/')[-1].replace('.html', ''),
                    "origin_name": title,
                    "thumb_url": urljoin(self.base, img) if img else "",
                    "poster_url": urljoin(self.base, img) if img else "",
                    "year": 2024,
                    "episode_current": label_text,
                    "quality": "HD",
                    "lang": "Vietsub",
                    "link_detail": urljoin(self.base, href)
                })
        except Exception as e:
            logger.error(f"Error at {path}: {e}")
        return items

    def run(self):
        logger.info("🚀 Scraping YanHH3D...")
        
        # Cấu trúc JSON theo chuẩn app media (Giống chuẩn OPhim bạn đưa)
        result = {
            "status": True,
            "items": self.get_items("/"), # Lấy trang chủ làm mặc định
            "pathImage": "",
            "pagination": {
                "totalItems": 100,
                "totalItemsPerPage": 20,
                "currentPage": 1,
                "totalPages": 5
            }
        }

        # Bổ sung các danh mục như app yêu cầu
        # Chuyển đổi sang định dạng Category -> Channels cho app của bạn
        app_data = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": [
                {
                    "name": "🔥 Mới cập nhật",
                    "channels": result["items"]
                },
                {
                    "name": "🎭 Hoạt Hình 3D",
                    "channels": self.get_items("/the-loai/hoat-hinh-3d")
                }
            ]
        }

        with open(CONFIG["output_file"], "w", encoding="utf-8") as f:
            json.dump(app_data, f, ensure_ascii=False, indent=2)
        logger.info(f"✅ Exported to {CONFIG['output_file']}")

if __name__ == "__main__":
    YanHH3DScraper().run()
