#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re, time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

class YanHH3DScraper:
    def __init__(self):
        self.base_url = "https://yanhh3d.bz"
        self.output_file = "yanhh3d.json"
        self.session = requests.Session()
        # Bộ Header này cực kỳ quan trọng để "giả danh" người dùng thật
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.google.com/", # Giả vờ đến từ Google
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        })

    def get_movies(self, path):
        movies = []
        url = urljoin(self.base_url, path)
        try:
            # Nghỉ 2 giây giữa các lần gọi để tránh bị quét tần suất (Rate limit)
            time.sleep(2)
            response = self.session.get(url, timeout=20, allow_redirects=True)
            
            if response.status_code != 200:
                logger.error(f"⚠️ Web chặn (Code: {response.status_code}) tại {url}")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            
            # Selector đặc biệt cho web YanHH: Tìm trong thẻ .flw-item hoặc tất cả link có chứa /phim/
            # Chúng ta sẽ quét tất cả thẻ <a> trước, sau đó tìm ngược ra ngoài
            links = soup.find_all('a', href=re.compile(r'/phim/[^/]+'))
            
            for a in links:
                href = a.get('href')
                # Lấy thẻ chứa (parent) để tìm ảnh và nhãn
                parent = a.find_parent(class_=re.compile(r'item|post|film')) or a.find_parent()
                
                title = a.get('title') or a.get_text(strip=True)
                if not title or len(title) < 5: continue # Bỏ qua link rác (ví dụ link "Xem thêm")

                # Tìm ảnh
                img_tag = parent.find('img') if parent else None
                img_url = ""
                if img_tag:
                    img_url = img_tag.get('data-src') or img_tag.get('src') or img_tag.get('data-original')

                # Tìm trạng thái
                status = "HD"
                status_tag = parent.find(class_=re.compile(r'label|status|eps|tick')) if parent else None
                if status_tag:
                    status = status_tag.get_text(strip=True)

                # Check trùng
                full_link = urljoin(self.base_url, href)
                if any(m['link_detail'] == full_link for m in movies):
                    continue

                movies.append({
                    "name": title,
                    "slug": href.strip('/').split('/')[-1],
                    "thumb_url": urljoin(self.base_url, img_url) if img_url else "",
                    "episode_current": status,
                    "link_detail": full_link
                })
        except Exception as e:
            logger.error(f"❌ Lỗi: {e}")
        
        return movies

    def run(self):
        logger.info("🎬 Đang cào YanHH3D...")
        
        # Thử lấy từ trang danh sách (Trang này thường ít bị Cloudflare siết chặt hơn trang chủ)
        targets = [
            ("Mới cập nhật", "/danh-sach/phim-moi"),
            ("Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("Hoàn thành", "/trang-thai/hoan-thanh")
        ]
        
        categories = []
        for name, path in targets:
            logger.info(f"🔎 Quét mục: {name}")
            channels = self.get_movies(path)
            # Nếu mục đó rỗng, thử quét lại ở đường dẫn khác (Fallback)
            if not channels and path == "/danh-sach/phim-moi":
                channels = self.get_movies("/")

            categories.append({
                "name": name,
                "channels": channels
            })

        output = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": categories,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in categories)
        logger.info(f"✅ Xong! Tìm thấy tổng cộng {total} phim.")

if __name__ == "__main__":
    YanHH3DScraper().run()
