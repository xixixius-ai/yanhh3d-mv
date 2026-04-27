#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format="%(asctime)s: %(message)s")
logger = logging.getLogger(__name__)

class YanHH3DScraper:
    def __init__(self):
        self.base_url = "https://yanhh3d.bz"
        self.output_file = "yanhh3d.json"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://yanhh3d.bz/"
        })

    def get_movies(self, path):
        movies = []
        url = urljoin(self.base_url, path)
        try:
            response = self.session.get(url, timeout=20)
            if response.status_code != 200:
                logger.error(f"⚠️ Status {response.status_code} at {url}")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            
            # CẤU TRÚC CHÍNH XÁC CỦA YANHH3D:
            # Mỗi bộ phim nằm trong thẻ <article> có class "item-list" hoặc tương tự
            articles = soup.find_all('article')
            
            for art in articles:
                # 1. Lấy tên và link từ thẻ <h2>
                h2_tag = art.find('h2')
                if not h2_tag: continue
                a_tag = h2_tag.find('a')
                if not a_tag: continue
                
                title = a_tag.get_text(strip=True)
                href = a_tag.get('href')

                # 2. Lấy ảnh bìa
                img_tag = art.find('img')
                img_url = ""
                if img_tag:
                    # Web này dùng Lazyload, ảnh thật nằm ở data-src hoặc src
                    img_url = img_tag.get('data-src') or img_tag.get('src')

                # 3. Lấy trạng thái (Số tập/HD)
                # Thường nằm ở thẻ span trong .post-thumbnail hoặc các tag overlay
                status = "HD"
                status_tag = art.find('span', class_=re.compile(r'label|status|episode|quality'))
                if status_tag:
                    status = status_tag.get_text(strip=True)

                if title and href:
                    movies.append({
                        "name": title,
                        "slug": href.rstrip('/').split('/')[-1],
                        "thumb_url": urljoin(self.base_url, img_url) if img_url else "",
                        "episode_current": status,
                        "link_detail": urljoin(self.base_url, href)
                    })

        except Exception as e:
            logger.error(f"❌ Error: {e}")
        
        return movies

    def run(self):
        logger.info("🎬 Đang quét YanHH3D (Cấu trúc Article)...")
        
        # Cào các chuyên mục chính
        cat_map = [
            ("Mới cập nhật", "/"),
            ("Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("Hoạt Hình 2D", "/the-loai/hoat-hinh-2d"),
            ("Phim Hoàn Thành", "/trang-thai/hoan-thanh")
        ]
        
        categories = []
        for name, path in cat_map:
            logger.info(f"🔎 Quét: {name}")
            channels = self.get_movies(path)
            categories.append({
                "name": name,
                "channels": channels
            })

        # Định dạng JSON cho App
        output = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": categories,
            "updated_at": re.sub(r'\.\d+', '', os.popen('date -Iseconds').read().strip())
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in categories)
        logger.info(f"✅ Thành công! Đã lấy được {total} phim.")

if __name__ == "__main__":
    YanHH3DScraper().run()
