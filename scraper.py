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
        # Giả lập Browser xịn nhất để tránh Cloudflare
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9",
            "Referer": "https://yanhh3d.bz/",
            "Upgrade-Insecure-Requests": "1"
        })

    def get_movies(self, path):
        movies = []
        url = urljoin(self.base_url, path)
        try:
            # Bypass cache bằng cách thêm tham số ngẫu nhiên
            response = self.session.get(f"{url}?t={os.urandom(4).hex()}", timeout=30)
            
            if response.status_code != 200:
                logger.error(f"❌ Bị chặn! Status: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            
            # CHIẾN THUẬT VÉT CẠN: Tìm tất cả thẻ <a> có đường dẫn chứa "/phim/"
            all_links = soup.find_all('a', href=re.compile(r'/phim/'))
            
            processed_slugs = set()

            for a in all_links:
                href = a.get('href')
                # Tách slug: /phim/dau-la-dai-luc/ -> dau-la-dai-luc
                slug_match = re.search(r'/phim/([^/]+)', href)
                if not slug_match: continue
                slug = slug_match.group(1).replace('.html', '')

                if slug in processed_slugs or slug == "danh-sach": continue
                
                # Tìm tiêu đề: Ưu tiên title, sau đó đến text bên trong
                title = a.get('title') or a.get_text(strip=True)
                if not title or len(title) < 5:
                    # Nếu thẻ a không có chữ, tìm thẻ h2/h3 lân cận
                    parent = a.find_parent(['h2', 'h3', 'div'])
                    title = parent.get_text(strip=True) if parent else title

                if not title or len(title) < 5: continue

                # Tìm ảnh: Quét trong thẻ a hoặc thẻ cha của nó
                img_url = ""
                img_tag = a.find('img') or (a.find_parent().find('img') if a.find_parent() else None)
                if img_tag:
                    img_url = img_tag.get('data-src') or img_tag.get('src') or img_tag.get('data-lazy-src')

                # Tìm nhãn (tập phim)
                status = "HD"
                label_tag = a.find_parent().find(class_=re.compile(r'label|ep|status|tick')) if a.find_parent() else None
                if label_tag:
                    status = label_tag.get_text(strip=True)

                movies.append({
                    "name": title,
                    "slug": slug,
                    "thumb_url": urljoin(self.base_url, img_url) if img_url else "",
                    "episode_current": status,
                    "link_detail": urljoin(self.base_url, href)
                })
                processed_slugs.add(slug)

        except Exception as e:
            logger.error(f"❌ Lỗi thực thi: {e}")
        
        return movies

    def run(self):
        logger.info("🚀 Đang khởi động Scraper YanHH3D...")
        
        categories = [
            {"name": "🔥 Mới cập nhật", "path": "/"},
            {"name": "🎭 Hoạt Hình 3D", "path": "/the-loai/hoat-hinh-3d"},
            {"name": "🌟 Phim Hoàn Thành", "path": "/trang-thai/hoan-thanh"}
        ]
        
        final_data = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": []
        }

        for cat in categories:
            logger.info(f"🔎 Đang lấy dữ liệu mục: {cat['name']}")
            channels = self.get_movies(cat['path'])
            final_data["categories"].append({
                "name": cat['name'],
                "channels": channels
            })

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in final_data["categories"])
        logger.info(f"✅ Hoàn tất! Xuất {total} phim ra file {self.output_file}")

if __name__ == "__main__":
    YanHH3DScraper().run()
