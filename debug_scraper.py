#!/usr/bin/env python3
"""
debug_scraper.py — Xem HTML thực tế mà scraper nhận được từ yanhh3d.bz
Chạy: python debug_scraper.py
"""

import requests
import re
from bs4 import BeautifulSoup

BASE_URL = "https://yanhh3d.bz"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
}

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    print(f"📡 {url} → Status: {r.status_code}")
    return r.text

def main():
    print("═" * 60)
    print("🔍 DEBUG: YanHH3D HTML Structure Analyzer")
    print("═" * 60)
    
    # 1. Test listing page
    print("\n[1] Listing page: /moi-cap-nhat")
    html = fetch(f"{BASE_URL}/moi-cap-nhat")
    soup = BeautifulSoup(html, "html.parser")
    
    # Tìm tất cả link có pattern /ten-phim
    print("\n🔗 Sample movie links found:")
    for a in soup.find_all("a", href=True)[:30]:
        href = a["href"]
        if href.startswith("/") and "/tap-" not in href and "sever" not in href.lower():
            parts = href.strip("/").split("/")
            if len(parts) == 1 and parts[0] and parts[0] not in ["moi-cap-nhat", "the-loai"]:
                title = a.get_text(strip=True)[:40]
                print(f"   • {href:35s} → '{title}'")
    
    # 2. Test movie page
    print("\n[2] Movie page: /kiem-lai-phan-2")
    html = fetch(f"{BASE_URL}/kiem-lai-phan-2")
    soup = BeautifulSoup(html, "html.parser")
    
    # Tìm episode links
    print("\n🎬 Episode links found:")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tap-" in href.lower():
            label = a.get_text(strip=True) or "?"
            print(f"   • {href:50s} → '{label}'")
    
    # 3. Test episode page for stream URL
    print("\n[3] Episode page: /kiem-lai-phan-2/tap-1")
    html = fetch(f"{BASE_URL}/kiem-lai-phan-2/tap-1")
    
    # Search for .m3u8 / .mp4 in raw HTML
    print("\n🎥 Searching for stream URLs in HTML...")
    m3u8_matches = re.findall(r'https?://[^\s\'"<>]+?\.m3u8[^\s\'"<>]*', html)
    mp4_matches = re.findall(r'https?://[^\s\'"<>]+?\.mp4[^\s\'"<>]*', html)
    
    if m3u8_matches:
        print(f"✅ Found {len(m3u8_matches)} .m3u8 URL(s):")
        for url in m3u8_matches[:3]:
            print(f"   • {url[:120]}...")
    else:
        print("❌ No .m3u8 found in static HTML")
    
    if mp4_matches:
        print(f"✅ Found {len(mp4_matches)} .mp4 URL(s):")
        for url in mp4_matches[:3]:
            print(f"   • {url[:120]}...")
    
    # Search for JS patterns that might contain stream URL
    print("\n🔎 Searching for JS config patterns...")
    js_patterns = [
        r'["\']?(?:file|src|source|url)["\']?\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        r'sources\s*:\s*\[\s*\{[^}]*?file\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
    ]
    for pattern in js_patterns:
        matches = re.findall(pattern, html, re.I | re.S)
        if matches:
            print(f"✅ Pattern '{pattern[:40]}...' matched {len(matches)} time(s):")
            for m in matches[:2]:
                url = m if isinstance(m, str) else m[0]
                print(f"   • {url[:120]}...")
    
    # Check for iframe
    print("\n🖼️  Iframe elements found:")
    iframe_soup = BeautifulSoup(html, "html.parser")
    for iframe in iframe_soup.find_all("iframe", src=True)[:5]:
        print(f"   • {iframe['src'][:100]}")
    
    # Print HTML snippet around "player" keyword
    print("\n📋 HTML snippet around 'player' keyword:")
    idx = html.lower().find("player")
    if idx > 0:
        snippet = html[max(0, idx-300):idx+400].replace("\n", " ")[:600]
        print(f"   ...{snippet}...")

if __name__ == "__main__":
    main()
