#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

class YanHH3DScraper:
    def __init__(self):
        self.base_url = "https://yanhh3d.bz"
        self.output_file = "yanhh3d.json"
        # Khởi tạo scraper giả lập trình duyệt để vượt Cloudflare
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

    def get_items(self, path):
        items = []
        url = urljoin(self.base_url, path)
        try:
            # Gửi request lấy HTML
            response = self.scraper.get(url, timeout=30)
            if response.status_code != 200:
                logger.error(f"❌ Lỗi {response.status_code} tại {path}")
                return []

            soup = BeautifulSoup(response.text, "lxml")
            
            # Selector chính xác cho HalimThemes: mỗi phim bọc trong thẻ <article>
            articles = soup.select("article.item, div.item-list, .halim-item")
            
            for art in articles:
                # 1. Lấy link và tiêu đề
                a_tag = art.select_one("a")
                if not a_tag: continue
                
                href = a_tag.get('href', '')
                title = a_tag.get('title') or art.select_one(".entry-title").get_text(strip=True) if art.select_one(".entry-title") else ""
                
                if not title or "/phim/" not in href: continue

                # 2. Lấy ảnh (Lazy load attributes)
                img_tag = art.select_one("img")
                img_url = ""
                if img_tag:
                    img_url = img_tag.get('data-src') or img_tag.get('src') or img_tag.get('data-lazy-src')

                # 3. Lấy nhãn tập phim (Badge)
                status = "HD"
                status_tag = art.select_one(".status, .label, .episode")
                if status_tag:
                    status = status_tag.get_text(strip=True)

                items.append({
                    "name": title,
                    "slug": href.rstrip('/').split('/')[-1],
                    "origin_name": title,
                    "thumb_url": urljoin(self.base_url, img_url) if img_url else "",
                    "poster_url": urljoin(self.base_url, img_url) if img_url else "",
                    "episode_current": status,
                    "quality": "HD",
                    "lang": "Vietsub",
                    "link_detail": urljoin(self.base_url, href)
                })
        except Exception as e:
            logger.error(f"⚠️ Lỗi fetch: {e}")
        
        return items

    def run(self):
        logger.info(f"🚀 Bắt đầu cào {self.base_url}...")
        
        # Cấu trúc Category chuẩn cho Mon Player
        categories = [
            {
                "name": "🔥 Mới cập nhật",
                "channels": self.get_items("/")
            },
            {
                "name": "🎭 Hoạt Hình 3D",
                "channels": self.get_items("/the-loai/hoat-hinh-3d")
            },
            {
                "name": "🌟 Phim Hoàn Thành",
                "channels": self.get_items("/trang-thai/hoan-thanh")
            }
        ]

        # Filter bỏ những category rỗng
        valid_categories = [c for c in categories if len(c["channels"]) > 0]

        data = {
            "id": "yanhh3d",
            "name": "YanHH3D Hoạt Hình",
            "image": "https://yanhh3d.bz/favicon.ico",
            "categories": valid_categories
        }

        # Lưu file JSON
        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in valid_categories)
        logger.info(f"✅ Hoàn tất! Tìm thấy {total} phim. File: {self.output_file}")

if __name__ == "__main__":
    YanHH3DScraper().run()
