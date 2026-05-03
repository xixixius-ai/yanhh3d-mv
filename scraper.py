#!/usr/bin/env python3
"""
YanHH3D Scraper → MonPlayer JSON (v3.1)

ROOT CAUSE FIX:
  play-fb-v8 endpoint redirect đến Facebook CDN token URL.
  urllib gọi trực tiếp bị chặn vì không có cookie browser session.
  → Fix: dùng Playwright intercept để "bắt" redirect Location header
    trong context đã có cookie/session đúng từ trang tập phim.

ANTI-RATE-LIMIT (không cần proxy):
  - resolve_cdn_via_playwright(): mở play-fb-v8 URL trong tab Playwright
    → intercept network event để lấy CDN URL có cookie đúng
  - Context rotation sau mỗi BATCH_SIZE tập (reset session/fingerprint)
  - Forced cooldown + rotate khi fail liên tiếp, sau đó retry tập đó
  - Jitter delay có weight
  - --resume: bỏ qua slug đã có file detail rồi
"""

import argparse
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

try:
    from playwright_stealth import stealth_sync
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL":               "https://yanhh3d.bz",
    "OUTPUT_DIR":             "ophim",
    "LIST_FILE":              "ophim.json",
    "MAX_MOVIES":             24,
    "MAX_EPISODES":           None,
    "TIMEOUT_NAV":            30_000,
    "TIMEOUT_WAIT":           20_000,
    "RAW_BASE":               os.getenv(
        "RAW_BASE",
        "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main",
    ),
    "EP_DELAY_MIN":           3_500,   # ms
    "EP_DELAY_MAX":           6_500,
    "BATCH_SIZE":             15,      # rotate context sau bao nhiêu tập
    "BATCH_COOLDOWN":         22.0,    # seconds
    "CONSECUTIVE_FAIL_LIMIT": 4,
    "FAIL_ROTATE_WAIT_MIN":   18.0,
    "FAIL_ROTATE_WAIT_MAX":   32.0,
    "CDN_INTERCEPT_TIMEOUT":  20_000,  # ms timeout cho tab intercept
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

EXTRA_HEADERS = {
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":           "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":           "gzip, deflate, br",
    "Cache-Control":             "no-cache",
    "Pragma":                    "no-cache",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "none",
    "Sec-Fetch-User":            "?1",
    "Upgrade-Insecure-Requests": "1",
}


# ─────────────────────────────────────────────
# BROWSER HELPERS
# ─────────────────────────────────────────────

def _apply_stealth(page):
    if HAS_STEALTH:
        stealth_sync(page)
    else:
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN','vi','en-US'] });
            window.chrome = { runtime: {} };
            const _pq = window.navigator.permissions.query.bind(navigator.permissions);
            window.navigator.permissions.query = p =>
                p.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : _pq(p);
        """)


def create_context(browser):
    """Tạo browser context + page mới với UA ngẫu nhiên."""
    ua  = random.choice(USER_AGENTS)
    ctx = browser.new_context(
        user_agent=ua,
        viewport={
            "width":  random.choice([1280, 1366, 1440, 1920]),
            "height": random.choice([720, 768, 800, 900]),
        },
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh",
        extra_http_headers=EXTRA_HEADERS,
        java_script_enabled=True,
    )
    page = ctx.new_page()
    _apply_stealth(page)
    logger.info(f"   🆕 New context | UA: …{ua[-35:]}")
    return ctx, page


def _close_ctx(ctx):
    try:
        ctx.close()
    except Exception:
        pass


def _human_delay(min_ms: int = 300, max_ms: int = 900):
    base = random.uniform(min_ms / 1000, max_ms / 1000)
    if random.random() < 0.25:
        base += random.uniform(0.8, 2.5)
    time.sleep(base)


def _wait_for_cf(page, selector: str, timeout: int):
    try:
        page.wait_for_function(
            """() => !document.title.includes('Just a moment') &&
                    !document.querySelector('#challenge-running') &&
                    document.readyState === 'complete'""",
            timeout=15_000,
        )
    except Exception:
        pass
    page.wait_for_selector(selector, state="attached", timeout=timeout)


def _debug_page(page, label: str):
    try:
        logger.info(f"   [DBG:{label}] title='{page.title()}' url='{page.url}'")
        logger.info(f"   [DBG:{label}] html[:400]={page.content()[:400].replace(chr(10),' ')}")
    except Exception as exc:
        logger.info(f"   [DBG:{label}] {exc}")


# ─────────────────────────────────────────────
# CDN URL RESOLVER — Playwright intercept
# ─────────────────────────────────────────────

def _is_valid_fb_cdn(url: str) -> bool:
    if not url:
        return False
    ul = url.lower()
    return ("fbcdn" in ul or "facebook" in ul) and ".mp4" in ul


def resolve_cdn_via_playwright(context, proxy_url: str, ep_page_url: str) -> str | None:
    """
    Mở proxy_url trong tab Playwright của context hiện tại.
    Intercept network events để bắt:
      - Redirect Location header → fbcdn .mp4 URL
      - Request trực tiếp tới fbcdn

    Tại sao Playwright thay urllib:
      - Cookie/session từ yanhh3d.bz được đính kèm tự động
      - JS chạy được nếu endpoint cần render
      - Fingerprint browser nhất quán với session trước
    """
    cdn_found: list[str] = []  # dùng list để lambda có thể mutate

    def _on_request(req):
        if not cdn_found and _is_valid_fb_cdn(req.url):
            cdn_found.append(req.url)

    def _on_response(resp):
        if cdn_found:
            return
        # Bắt redirect
        if resp.status in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            if _is_valid_fb_cdn(loc):
                cdn_found.append(loc)
        # Bắt request fbcdn trực tiếp
        if _is_valid_fb_cdn(resp.url):
            cdn_found.append(resp.url)

    tab = context.new_page()
    try:
        tab.on("request",  _on_request)
        tab.on("response", _on_response)
        tab.set_extra_http_headers({"Referer": ep_page_url})

        try:
            tab.goto(
                proxy_url,
                wait_until="commit",   # không đợi load xong, chỉ cần commit
                timeout=CONFIG["CDN_INTERCEPT_TIMEOUT"],
            )
        except PlaywrightTimeout:
            pass  # timeout OK nếu đã intercept được URL
        except Exception as exc:
            logger.debug(f"   tab.goto play-fb-v8: {exc}")

        # Fallback 1: URL cuối của tab sau redirect
        if not cdn_found and _is_valid_fb_cdn(tab.url):
            cdn_found.append(tab.url)

        # Fallback 2: parse HTML body
        if not cdn_found:
            try:
                html = tab.content()
                for pat in [
                    r'"(https?://scontent-[^"]+\.mp4[^"]*)"',
                    r"'(https?://scontent-[^']+\.mp4[^']*)'",
                    r'url\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                    r'src\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                    r'file\s*:\s*["\']([^"\']*\.mp4[^"\']*)["\']',
                    r'(https?://[^\s\'"]+fbcdn[^\s\'"]+\.mp4[^\s\'"]*)',
                ]:
                    m = re.search(pat, html, re.IGNORECASE)
                    if m:
                        u = m.group(1).replace("\\/", "/")
                        if _is_valid_fb_cdn(u):
                            cdn_found.append(u)
                            break
            except Exception:
                pass

        return cdn_found[0] if cdn_found else None

    finally:
        try:
            tab.close()
        except Exception:
            pass


# ─────────────────────────────────────────────
# METADATA
# ─────────────────────────────────────────────

def get_movie_metadata(page, slug: str) -> dict:
    empty = {"description": "", "tags": [], "year": "", "status": "", "poster": "", "total_episodes": ""}
    try:
        _human_delay(200, 500)
        page.goto(f"{CONFIG['BASE_URL']}/{slug}",
                  wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "body", CONFIG["TIMEOUT_WAIT"])
        m = page.evaluate("""() => {
            const r = {description:'',tags:[],year:'',status:'',poster:'',total_episodes:''};
            r.description = (
                document.querySelector('meta[name="description"]')?.content ||
                document.querySelector('meta[property="og:description"]')?.content || ''
            ).trim().replace(/\s+/g,' ').slice(0,500);
            for (const l of document.querySelectorAll('.genres a,.film-info a[href*="/the-loai/"],.tick a')) {
                const t = l.innerText.trim();
                if (t && t.length < 50 && !/tap|tập/i.test(t)) r.tags.push(t);
            }
            r.tags = [...new Set(r.tags)].slice(0,10);
            const ym = document.title.match(/(\d{4})/) ||
                       document.querySelector('.film-info')?.innerText?.match(/(\d{4})/);
            if (ym) r.year = ym[1];
            const st = (document.querySelector('.tick-rate,.badge,.status')?.innerText||'').toLowerCase();
            if (/hoàn thành|end|completed/i.test(st))   r.status = 'completed';
            else if (/đang phát|ongoing|updating/i.test(st)) r.status = 'ongoing';
            r.poster = document.querySelector('meta[property="og:image"]')?.content ||
                       document.querySelector('.film-poster img')?.src ||
                       document.querySelector('.film-poster img')?.dataset.src || '';
            const ep = (document.querySelector(
                '.total-episodes,.episode-count,.film-info .fdi-item')?.innerText||'')
                .match(/(\d+)\s*(?:tập|ep)/i);
            if (ep) r.total_episodes = ep[1];
            return r;
        }""")
        if not m.get("description"):
            html = page.content()
            dm = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
            if dm:
                m["description"] = dm.group(1).strip()[:500]
        logger.info(f"   Meta: tags={m['tags'][:3]} status={m['status']}")
        return m
    except Exception as exc:
        logger.warning(f"   Metadata failed {slug}: {exc}")
        return empty


# ─────────────────────────────────────────────
# MOVIE LIST HELPERS
# ─────────────────────────────────────────────

def _extract_flw_items(page) -> list[dict]:
    return page.evaluate("""() => {
        const out = [];
        for (const item of document.querySelectorAll('.flw-item')) {
            const link = item.querySelector('.film-poster-ahref,.film-detail h3 a');
            if (!link?.href) continue;
            const slug  = link.href.split('/').pop().replace(/\\/$/,'');
            const title = link.innerText.trim() || link.title || '';
            if (!title || slug.includes('search')) continue;
            let thumb = item.querySelector('img[data-src],img.film-poster-img')?.dataset.src ||
                        item.querySelector('img[data-src],img.film-poster-img')?.src || '';
            if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
            const badge = item.querySelector('.tick.tick-rate,.fdi-item')?.innerText.trim() || '';
            out.push({ slug, title, thumb, badge });
        }
        return out;
    }""")


def search_movies(page, keyword: str) -> list[dict]:
    try:
        page.goto(f"{CONFIG['BASE_URL']}/tim-kiem?keyword={keyword}",
                  wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        return _extract_flw_items(page)
    except Exception as exc:
        logger.error(f"Search failed: {exc}")
        return []


def list_all_movies(page, category_url: str = None) -> list[dict]:
    url = category_url or f"{CONFIG['BASE_URL']}/danh-sach/hoat-hinh"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        movies, page_num = [], 1
        while len(movies) < 200:
            batch = _extract_flw_items(page)
            if not batch:
                break
            movies.extend(batch)
            nxt = page.query_selector('a[title="Next"],.pagination li.active + li a')
            if not nxt or page_num >= 15:
                break
            page_num += 1
            nxt.click()
            _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
            _human_delay(500, 1200)
        return movies
    except Exception as exc:
        logger.error(f"List movies failed: {exc}")
        return []


def get_trending_movies(page, limit: int = 50) -> list[dict]:
    try:
        page.goto(CONFIG["BASE_URL"], wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, "homepage")
        _wait_for_cf(page, ".flw-item", CONFIG["TIMEOUT_WAIT"])
        return _extract_flw_items(page)[:limit]
    except Exception as exc:
        logger.error(f"Trending failed: {exc}")
        return []


# ─────────────────────────────────────────────
# EPISODE FUNCTIONS
# ─────────────────────────────────────────────

def get_latest_ep_url(page, slug: str) -> str | None:
    try:
        _human_delay(300, 700)
        page.goto(f"{CONFIG['BASE_URL']}/{slug}",
                  wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, f"detail-{slug}")
        _wait_for_cf(page, f"a[href*='/{slug}/tap-']", CONFIG["TIMEOUT_WAIT"])
        return page.evaluate(f"""() => {{
            const links = Array.from(document.querySelectorAll('a[href*="/{slug}/tap-"]'))
                .filter(a => !a.href.includes('/sever2/'))
                .map(a => ({{ href:a.href, num:parseInt((a.href.match(/tap-(\\d+)/)||[])[1]||'0') }}))
                .filter(a => a.num > 0).sort((a,b) => b.num - a.num);
            return links.length ? links[0].href : null;
        }}""")
    except Exception as exc:
        logger.warning(f"   detail page error {slug}: {exc}")
        return None


def get_episodes(page, slug: str) -> list[dict]:
    latest = get_latest_ep_url(page, slug)
    if not latest:
        return []
    try:
        _human_delay(400, 900)
        page.goto(latest, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _debug_page(page, f"ep-{slug}")
        _wait_for_cf(page, "#episodes-content", CONFIG["TIMEOUT_WAIT"])
        eps = page.evaluate("""() => {
            const out = [];
            const pane = document.querySelector('#top-comment');
            if (!pane) return out;
            for (const item of pane.querySelectorAll('a.ssl-item.ep-item')) {
                const href = item.href || '';
                const text = (item.querySelector('.ssli-order')?.innerText ||
                              item.querySelector('.ep-name')?.innerText ||
                              item.title || '').trim();
                if (href.includes('/sever2/')) continue;
                if (href && /^\d+$/.test(text)) out.push({ name:text, url:href, num:parseInt(text) });
            }
            return out.sort((a,b) => b.num - a.num);
        }""")
        logger.info(f"   {len(eps)} episodes found")
        return eps
    except Exception as exc:
        logger.warning(f"   episodes error {latest}: {exc}")
        return []


def get_stream_url(page, ctx, ep_url: str) -> list[dict] | None:
    """
    Lấy stream URL cho 1 tập.
    Truyền ctx để resolve_cdn_via_playwright dùng cùng cookie session.
    """
    try:
        _human_delay(300, 700)
        page.goto(ep_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_NAV"])
        _wait_for_cf(page, "#list_sv", CONFIG["TIMEOUT_WAIT"])

        buttons = page.evaluate("""() => {
            const out = [];
            for (const btn of document.querySelectorAll('#list_sv a.btn3dsv')) {
                const src   = (btn.getAttribute('data-src') || '').trim();
                const label = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                if (src) out.push({ src, label });
            }
            return out;
        }""")

        preferred  = [b for b in buttons if any(x in b["label"] for x in ["hd", "1080", "4k"])]
        candidates = preferred if preferred else buttons

        for btn in candidates:
            if "play-fb-v8" not in btn["src"]:
                continue
            # KEY FIX: Playwright intercept thay urllib
            cdn = resolve_cdn_via_playwright(ctx, btn["src"], ep_url)
            if cdn and _is_valid_fb_cdn(cdn):
                label = f"fb-cdn-{btn['label']}" if btn["label"] else "fb-cdn"
                logger.debug(f"   CDN: {cdn[:80]}…")
                return [{"url": cdn, "type": "mp4", "label": label}]

        return None
    except Exception as exc:
        logger.debug(f"   get_stream_url: {exc}")
        return None


# ─────────────────────────────────────────────
# BUILD JSON OUTPUT
# ─────────────────────────────────────────────

def _build_search_str(movie: dict, meta: dict = None) -> str:
    meta  = meta or {}
    parts = [
        movie.get("title", ""),
        " ".join(meta.get("tags", [])),
        meta.get("description", ""),
        movie.get("slug", "").replace("-", " "),
        "hoạt hình trung quốc", "thuyết minh", "anime", "donghua",
    ]
    return " ".join(p for p in parts if p).lower().strip()


def build_detail_json(slug: str, episodes: list, meta: dict = None) -> dict:
    meta    = meta or {}
    streams = []
    for i, ep in enumerate(episodes):
        raw = ep.get("stream")
        if not raw:
            continue
        links = [
            {"id": f"{slug}--0-{i}-{j}", "name": s.get("label") or f"Link {j+1}",
             "type": s["type"], "default": j == 0, "url": s["url"]}
            for j, s in enumerate(raw)
        ]
        streams.append({"id": f"{slug}--0-{i}", "name": ep["name"], "stream_links": links})
    result = {
        "sources": [{
            "id": f"{slug}--0", "name": "Thuyet Minh #1",
            "contents": [{"id": f"{slug}--0", "name": "", "grid_number": 3, "streams": streams}],
        }],
        "subtitle": "Thuyet Minh",
        "search":   _build_search_str({"slug": slug, "title": slug}, meta),
        "tags":     meta.get("tags", []),
        "description": meta.get("description", ""),
    }
    for k in ("year", "status", "total_episodes"):
        if meta.get(k):
            result[k] = meta[k]
    return result


def build_list_item(movie: dict, meta: dict = None) -> dict:
    meta  = meta or {}
    thumb = movie.get("thumb") or meta.get("poster", "")
    badge = movie.get("badge") or meta.get("status", "")
    item  = {
        "id": movie["slug"], "name": movie["title"],
        "search":      _build_search_str(movie, meta),
        "keywords":    meta.get("tags", []),
        "description": meta.get("description", ""),
        "image":       {"url": thumb, "type": "cover", "width": 480, "height": 640},
        "type":        "playlist",
        "display":     "text-below",
        "label":       {"text": badge or "Trending", "position": "top-left",
                        "color": "#35ba8b", "text_color": "#ffffff"},
        "remote_data": {"url": f"{CONFIG['RAW_BASE']}/ophim/detail/{movie['slug']}.json"},
        "enable_detail": True,
    }
    for k in ("year", "status"):
        if meta.get(k):
            item[k] = meta[k]
    return item


# ─────────────────────────────────────────────
# CORE SCRAPER — context rotation
# ─────────────────────────────────────────────

def scrape_movie(browser, movie_info: dict, max_episodes: int = None) -> tuple | None:
    """
    Scrape 1 bộ phim.

    Context rotation strategy (không cần proxy):
      - Tạo context mới khi bắt đầu mỗi bộ phim
      - Rotate sau mỗi BATCH_SIZE tập → reset cookie/session/fingerprint
      - Fail liên tiếp >= limit → rotate ngay + retry tập vừa fail
    """
    if max_episodes is None:
        max_episodes = CONFIG["MAX_EPISODES"]

    slug = movie_info["slug"]
    logger.info(f"  ▶ {slug}")

    ctx, page = create_context(browser)
    try:
        meta          = get_movie_metadata(page, slug)
        meta["title"] = movie_info.get("title", slug)
        meta["thumb"] = movie_info.get("thumb", "")
        meta["badge"] = movie_info.get("badge", "")

        episodes = get_episodes(page, slug)
        if not episodes:
            logger.warning(f"  No episodes for {slug}")
            return None

        limit            = len(episodes) if max_episodes is None else min(len(episodes), max_episodes)
        ep_data          = []
        consecutive_fail = 0

        for i in range(limit):
            ep = episodes[i]

            # ── Batch rotation ──────────────────────────────
            if i > 0 and i % CONFIG["BATCH_SIZE"] == 0:
                logger.info(
                    f"    🔄 Batch {i // CONFIG['BATCH_SIZE']} done "
                    f"({i} tập). Cooldown {CONFIG['BATCH_COOLDOWN']}s → new context…"
                )
                time.sleep(CONFIG["BATCH_COOLDOWN"])
                _close_ctx(ctx)
                ctx, page        = create_context(browser)
                consecutive_fail = 0
            # ───────────────────────────────────────────────

            _human_delay(CONFIG["EP_DELAY_MIN"], CONFIG["EP_DELAY_MAX"])
            stream = get_stream_url(page, ctx, ep["url"])

            # ── Fail handler ────────────────────────────────
            if not stream:
                consecutive_fail += 1
                logger.warning(f"    ✗ Tập {ep['name']} — no stream (streak {consecutive_fail})")

                if consecutive_fail >= CONFIG["CONSECUTIVE_FAIL_LIMIT"]:
                    wait = random.uniform(
                        CONFIG["FAIL_ROTATE_WAIT_MIN"],
                        CONFIG["FAIL_ROTATE_WAIT_MAX"],
                    )
                    logger.warning(
                        f"    ⚡ {consecutive_fail} fails → rotate context. Wait {wait:.0f}s…"
                    )
                    time.sleep(wait)
                    _close_ctx(ctx)
                    ctx, page        = create_context(browser)
                    consecutive_fail = 0

                    # Retry tập này với context mới
                    _human_delay(CONFIG["EP_DELAY_MIN"], CONFIG["EP_DELAY_MAX"])
                    stream = get_stream_url(page, ctx, ep["url"])
                    if stream:
                        logger.info(f"    ✓ Tập {ep['name']} — OK (after context rotate)")

                if not stream:
                    ep_data.append({
                        "name": ep["name"],
                        "stream": [{
                            "id": f"{slug}--0-{i}-err", "name": f"{ep['name']}(no stream)",
                            "type": "error", "default": False, "url": "error:no_stream",
                        }],
                    })
                    continue
            # ───────────────────────────────────────────────

            ep_data.append({"name": ep["name"], "stream": stream})
            consecutive_fail = 0
            logger.info(f"    ✓ Tập {ep['name']}: OK")

        detail = build_detail_json(slug, ep_data, meta)
        li     = build_list_item(movie_info, meta)
        ok     = sum(1 for ep in ep_data if any(s["type"] != "error" for s in ep["stream"]))
        logger.info(f"  ✅ {slug} — {ok}/{len(ep_data)} playable")
        return li, detail

    finally:
        _close_ctx(ctx)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YanHH3D → MonPlayer Scraper v3.1")
    parser.add_argument("--search",       type=str)
    parser.add_argument("--slug",         type=str)
    parser.add_argument("--url",          type=str)
    parser.add_argument("--list-all",     action="store_true")
    parser.add_argument("--trending",     action="store_true")
    parser.add_argument("--max-movies",   type=int, default=CONFIG["MAX_MOVIES"])
    parser.add_argument("--max-episodes", type=int, default=None)
    parser.add_argument("--output",       type=str, default=CONFIG["OUTPUT_DIR"])
    parser.add_argument("--resume",       action="store_true",
                        help="Bỏ qua slug đã có file detail JSON")
    args = parser.parse_args()

    CONFIG["OUTPUT_DIR"]   = args.output
    CONFIG["MAX_MOVIES"]   = args.max_movies
    CONFIG["MAX_EPISODES"] = args.max_episodes

    logger.info("YanHH3D → MonPlayer v3.1  (Playwright CDN intercept + context rotation)")
    detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)
    channels: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--lang=vi-VN",
            ],
        )
        list_ctx, list_page = create_context(browser)

        def _save(movie: dict, li: dict, dj: dict):
            (detail_dir / f"{movie['slug']}.json").write_text(
                json.dumps(dj, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            channels.append(li)

        def process_list(movies: list[dict]):
            for movie in movies[: args.max_movies]:
                slug = movie.get("slug", "")
                if args.resume and (detail_dir / f"{slug}.json").exists():
                    logger.info(f"  ⏭ Skip {slug} (exists)")
                    try:
                        dj = json.loads((detail_dir / f"{slug}.json").read_text())
                        channels.append(build_list_item(movie, {
                            "tags":        dj.get("tags", []),
                            "description": dj.get("description", ""),
                            "year":        dj.get("year", ""),
                            "status":      dj.get("status", ""),
                        }))
                    except Exception:
                        pass
                    continue
                try:
                    res = scrape_movie(browser, movie, args.max_episodes)
                    if res:
                        _save(movie, *res)
                except Exception as exc:
                    logger.error(f"  Error {slug}: {exc}")

        try:
            if args.search:
                process_list(search_movies(list_page, args.search))
            elif args.slug:
                fake = {"slug": args.slug, "title": args.slug, "thumb": "", "badge": ""}
                res  = scrape_movie(browser, fake, args.max_episodes)
                if res:
                    _save(fake, *res)
            elif args.url:
                slug = args.url.rstrip("/").split("/")[-1]
                fake = {"slug": slug, "title": slug, "thumb": "", "badge": ""}
                res  = scrape_movie(browser, fake, args.max_episodes)
                if res:
                    _save(fake, *res)
            elif args.list_all:
                process_list(list_all_movies(list_page))
            else:
                movies = get_trending_movies(list_page, limit=max(50, args.max_movies))
                logger.info(f"Found {len(movies)} trending → processing {min(len(movies), args.max_movies)}")
                process_list(movies)
        finally:
            _close_ctx(list_ctx)
            browser.close()

    out = {
        "id": "yanhh3d-thuyet-minh", "name": "YanHH3D - Thuyet Minh",
        "url": f"{CONFIG['RAW_BASE']}/ophim",
        "search": True, "enable_search": True, "features": {"search": True},
        "color":   "#004444",
        "image":   {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description":  "Phim thuyet minh chat luong cao tu YanHH3D.bz",
        "grid_number":  3,
        "channels":     channels,
        "sorts": [{"text": "Moi nhat", "type": "radio", "url": f"{CONFIG['RAW_BASE']}/ophim"}],
        "meta": {
            "source":      CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version":     "3.1",
        },
    }
    Path(CONFIG["LIST_FILE"]).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"✅ Done — {CONFIG['LIST_FILE']} + {len(channels)} detail files.")


if __name__ == "__main__":
    main()
