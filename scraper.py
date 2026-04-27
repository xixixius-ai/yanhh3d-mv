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
        # Headers mô phỏng trình duyệt thật để tránh bị chặn (403 Forbidden)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://yanhh3d.bz/",
            "Cache-Control": "max-age=0"
        })

    def get_movies(self, path):
        movies = []
        url = urljoin(self.base_url, path)
        try:
            # Bypass một số bước kiểm tra đơn giản của Cloudflare/Firewall
            response = self.session.get(url, timeout=15)
            logger.info(f"Đang quét {url} - Status: {response.status_code}")
            
            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            
            # Quét tất cả các thẻ có khả năng chứa phim (mở rộng selector)
            cards = soup.find_all(['div', 'li', 'a'], class_=re.compile(r'item|film|movie|post'))
            
            for card in cards:
                # Tìm link phim
                link_tag = card.find('a', href=True) if card.name != 'a' else card
                if not link_tag or '/phim/' not in link_tag['href']:
                    continue
                
                # Trích xuất dữ liệu
                title = link_tag.get('title') or card.get_text(strip=True)
                href = link_tag['href']
                
                # Tìm ảnh (quét nhiều thuộc tính lazy load)
                img_tag = card.find('img')
                img_url = ""
                if img_tag:
                    img_url = img_tag.get('data-src') or img_tag.get('data-original') or img_tag.get('src') or ""

                # Tìm số tập/trạng thái
                status = "HD"
                status_tag = card.find(class_=re.compile(r'label|status|episode|tick'))
                if status_tag:
                    status = status_tag.get_text(strip=True)

                # Tránh trùng lặp phim
                if any(m['link_detail'] == urljoin(self.base_url, href) for m in movies):
                    continue

                if title and len(title) > 2:
                    movies.append({
                        "name": title,
                        "slug": href.strip('/').split('/')[-1],
                        "thumb_url": urljoin(self.base_url, img_url) if img_url else "",
                        "episode_current": status,
                        "link_detail": urljoin(self.base_url, href)
                    })
        except Exception as e:
            logger.error(f"Lỗi: {e}")
        
        return movies

    def run(self):
        logger.info("🚀 Bắt đầu cào...")
        
        # Danh mục cần lấy
        cat_map = [
            ("Mới cập nhật", "/"),
            ("Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("Phim Bộ Hoàn Thành", "/trang-thai/hoan-thanh")
        ]
        
        categories = []
        for name, path in cat_map:
            logger.info(f"--- Lấy mục: {name} ---")
            channels = self.get_movies(path)
            categories.append({
                "name": name,
                "channels": channels
            })

        # Cấu trúc JSON chuẩn App Media
        output = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": categories
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        logger.info(f"✅ Hoàn tất! Đã xuất file {self.output_file}")

if __name__ == "__main__":
    YanHH3DScraper().run()
