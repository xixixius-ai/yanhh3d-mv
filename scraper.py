#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re
import requests

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

class YanHH3DScraper:
    def __init__(self):
        self.api_url = "https://yanhh3d.bz/wp-json/wp/v2/posts"
        self.output_file = "yanhh3d.json"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        })

    def get_movies_api(self, category_id=None):
        movies = []
        # Gọi API lấy 40 bài viết mới nhất
        params = {
            "per_page": 40,
            "_embed": 1 # Lấy luôn ảnh đại diện và metadata
        }
        if category_id:
            params["categories"] = category_id

        try:
            r = self.session.get(self.api_url, params=params, timeout=20)
            if r.status_code != 200:
                logger.error(f"❌ API Error: {r.status_code}")
                return []
            
            posts = r.json()
            for post in posts:
                # Lấy ảnh từ field _embedded
                thumb = ""
                if "_embedded" in post and "wp:featuredmedia" in post["_embedded"]:
                    thumb = post["_embedded"]["wp:featuredmedia"][0].get("source_url", "")

                movies.append({
                    "name": post["title"]["rendered"],
                    "slug": post["slug"],
                    "thumb_url": thumb,
                    "episode_current": "Update", # API thường không có field này sẵn, để mặc định
                    "link_detail": post["link"]
                })
        except Exception as e:
            logger.error(f"⚠️ API Fetch Fail: {e}")
        return movies

    def run(self):
        logger.info("🚀 Đang truy vấn API YanHH3D...")
        
        # WP-JSON API không cần cào HTML, cực kỳ ổn định
        categories = [
            {"name": "🔥 Mới cập nhật", "id": None},
            {"name": "🎭 Hoạt Hình 3D", "id": 1}, # ID 1 thường là mặc định hoặc bạn có thể bỏ qua ID để lấy chung
        ]
        
        final_categories = []
        for cat in categories:
            logger.info(f"🔎 Đang lấy dữ liệu mục: {cat['name']}")
            channels = self.get_movies_api(cat['id'])
            final_categories.append({
                "name": cat['name'],
                "channels": channels
            })

        output = {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "categories": final_categories
        }

        with open(self.output_file, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
            
        total = sum(len(c['channels']) for c in final_categories)
        logger.info(f"✅ Hoàn tất! Tìm thấy {total} phim.")

if __name__ == "__main__":
    YanHH3DScraper().run()
