#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re
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
                logger.error(f"⚠️ Lỗi kết nối {url} (Status: {response.status_code})")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            
            # CẤU TRÚC THỰC TẾ: Các phim nằm trong thẻ .item
            # Thường nằm trong các container như .list-films hoặc .items
            cards = soup.select(".list-films .item, .items .item, .list-items .item")
            
            # Nếu không tìm thấy bằng class cụ thể, quét toàn bộ .item có chứa link /phim/
            if not cards:
                cards = [i for i in soup.select(".item") if i.find("a", href=re.compile(r'/phim/'))]

            for card in cards:
                # 1. Tìm tiêu đề và link trong thẻ h3 (YanHH thường dùng h3 cho tên phim)
                name_tag = card.select_one("h3 a, .title a, a[href*='/phim/']")
                if not name_tag: continue
                
                title = name_tag.get("title") or name_tag.get_text(strip=True)
                href = name_tag.get("href")

                # 2. Tìm ảnh trong thẻ .poster hoặc .thumb
                img_tag = card.find("img")
                img_url = ""
                if img_tag:
                    # Ưu tiên các attribute lazyload
                    img_url = img_tag.get("data-src") or img_tag.get("src") or img_tag.get("data-original")

                # 3. Tìm số tập (Label) - Thường trong thẻ .label hoặc .ep (Ví dụ: "Tập 20/40")
                status = "HD"
                status_tag = card.select_one(".label, .ep, .status, .label-ep")
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
            logger.error(f"❌ Lỗi: {e}")
        
        return movies

    def run(self):
        logger.info("🎬 Đang quét YanHH3D theo cấu trúc .list-films .item...")
        
        # Các mục tiêu cụ thể
        targets = [
            ("Mới cập nhật", "/"),
            ("Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("Hoàn thành", "/trang-thai/hoan-thanh")
        ]
        
        categories = []
        for name, path in targets:
            logger.info(f"🔎 Quét danh mục: {name}")
            channels = self.get_movies(path)
            categories.append({
                "name": name,
                "channels": channels
            })

        output = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": categories,
            "created_at": os.popen('date').read().strip()
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in categories)
        logger.info(f"✅ Hoàn tất! Tìm thấy {total} phim.")

if __name__ == "__main__":
    YanHH3DScraper().run()
