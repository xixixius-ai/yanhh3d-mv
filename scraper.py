#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re
import cloudscraper # Sử dụng cloudscraper để vượt Cloudflare
from bs4 import BeautifulSoup
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format="%(asctime)s: %(message)s")
logger = logging.getLogger(__name__)

class YanHH3DScraper:
    def __init__(self):
        self.base_url = "https://yanhh3d.bz"
        self.output_file = "yanhh3d.json"
        # Khởi tạo scraper tự động giải quyết Cloudflare challenge
        self.scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )

    def get_movies(self, path):
        movies = []
        url = urljoin(self.base_url, path)
        try:
            response = self.scraper.get(url, timeout=30)
            if response.status_code != 200:
                logger.error(f"❌ Lỗi truy cập: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, "lxml")
            
            # Selector đặc trị cho theme phim YanHH3D (thẻ article class item)
            articles = soup.select("article.item")
            
            for art in articles:
                # 1. Lấy thẻ a chứa link và tiêu đề
                a_tag = art.select_one("a")
                if not a_tag: continue
                
                href = a_tag.get('href', '')
                title = a_tag.get('title') or art.select_one(".entry-title").get_text(strip=True) if art.select_one(".entry-title") else ""

                # 2. Lấy ảnh (HalimTheme thường để ảnh ở data-src hoặc style background)
                img_tag = art.select_one(".post-thumbnail img")
                img_url = ""
                if img_tag:
                    img_url = img_tag.get('data-src') or img_tag.get('src') or img_tag.get('data-lazy-src')

                # 3. Lấy nhãn tập phim (ví dụ: Tập 15/20)
                status = "HD"
                status_tag = art.select_one(".label, .status, .episode")
                if status_tag:
                    status = status_tag.get_text(strip=True)

                if title and "/phim/" in href:
                    movies.append({
                        "name": title,
                        "slug": href.rstrip('/').split('/')[-1],
                        "thumb_url": urljoin(self.base_url, img_url) if img_url else "",
                        "episode_current": status,
                        "link_detail": urljoin(self.base_url, href)
                    })
        except Exception as e:
            logger.error(f"⚠️ Lỗi tại {path}: {e}")
        
        return movies

    def run(self):
        logger.info("🚀 Đang cào dữ liệu YanHH3D bằng CloudScraper...")
        
        # Cấu trúc Categories cho App Media
        cat_configs = [
            ("🔥 Mới cập nhật", "/"),
            ("🎭 Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("🌟 Phim Hoàn Thành", "/trang-thai/hoan-thanh")
        ]
        
        categories = []
        for name, path in cat_configs:
            logger.info(f"🔎 Đang lấy: {name}")
            channels = self.get_movies(path)
            categories.append({
                "name": name,
                "channels": channels
            })

        output = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": categories
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in categories)
        logger.info(f"✅ Hoàn tất! Tìm thấy {total} phim.")

if __name__ == "__main__":
    YanHH3DScraper().run()
