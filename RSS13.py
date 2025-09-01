import os
import sys
import subprocess
import tempfile
import re
import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# ===== GitHub 上の共通関数を一時ディレクトリにクローン =====
REPO_URL = "https://github.com/aiueo0306/shared-python-env.git"
SHARED_DIR = os.path.join(tempfile.gettempdir(), "shared-python-env")

if not os.path.exists(SHARED_DIR):
    print("🔄 共通関数を初回クローン中...")
    subprocess.run(["git", "clone", "--depth", "1", REPO_URL, SHARED_DIR], check=True)
else:
    print("🔁 共通関数を更新中...")
    subprocess.run(["git", "-C", SHARED_DIR, "pull"], check=True)

sys.path.append(SHARED_DIR)

# ===== 共通関数のインポート =====
from rss_utils import generate_rss
from scraper_utils import extract_items
from browser_utils import click_button_in_order

# ===== 固定情報（学会サイト） =====
BASE_URL = "https://medical.taisho.co.jp/medical/doctor-news/"
GAKKAI = "大正製薬（ニュースレター）"

SELECTOR_TITLE = "div._news_text_jzd1w_19"
title_selector = ""
title_index = 0
href_selector = "a"
href_index = 0
SELECTOR_DATE = "p.mantine-focus-auto._date_jzd1w_1.m_b6d8b162.mantine-Text-root"
date_selector = ""
date_index = 0
year_unit = "."
month_unit = "."
day_unit = ""
date_format = f"%Y{year_unit}%m{month_unit}%d{day_unit}"
date_regex = rf"(\d{{2,4}}){year_unit}(\d{{1,2}}){month_unit}(\d{{1,2}}){day_unit}"

# ===== ポップアップ順序クリック設定 =====
POPUP_MODE = 1
POPUP_BUTTONS = ["薬剤師"] if POPUP_MODE else []
WAIT_BETWEEN_POPUPS_MS = 500
BUTTON_TIMEOUT_MS = 12000

# ===== 実行 =====
with sync_playwright() as p:
    print("▶ ブラウザを起動中...")
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        locale="ja-JP",
        viewport={"width": 1366, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        extra_http_headers={"Accept-Language": "ja,en;q=0.8"},
    )

    # Bot検出の緩和
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    # 公開IP表示（任意）
    try:
        ip = context.request.get("https://api.ipify.org?format=json").json()
        print("Runner public IP:", ip)
    except Exception as e:
        print("IP check failed:", e)

    page = context.new_page()

    # ---------- stg -> prod リライト（必ず goto より前で、with の内側） ----------
    def _rewrite_stg_to_prod(route, request):
        url = request.url
        if "stg-medical2.taisho.co.jp" in url:
            new_url = url.replace("stg-medical2.taisho.co.jp", "medical.taisho.co.jp")
            route.continue_(url=new_url)  # ← これだけでOK
            print(f"🔁 rewrote {url} -> {new_url}")
        else:
            route.continue_()

    context.route("**/*", _rewrite_stg_to_prod)
    # -------------------------------------------------------------------------

    # ---------- ログ（ネットワーク/コンソール） ----------
    os.makedirs("netlog", exist_ok=True)

    def _tap(u: str) -> bool:
        patterns = ["/wp-json/", "/api/", "/ajax", "/doctor-news", "/news", "/wp-admin/admin-ajax.php"]
        return any(s in u for s in patterns)

    def _safe_name(u: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", u)[:180]

    def on_response(res):
        url = res.url
        if _tap(url):
            try:
                body = res.text()
            except Exception as e:
                body = f"<<read error: {e}>>"
            path = f"netlog/{_safe_name(url)}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"STATUS: {res.status}\nURL: {url}\n\n{body}")
            print("📥 captured:", path)

    def on_request_failed(req):
        if _tap(req.url):
            print("❌ request failed:", req.url, req.failure)

    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)

    console_log_path = "netlog/console.log"

    def on_console(msg):
        try:
            mtype = msg.type() if callable(getattr(msg, "type", None)) else getattr(msg, "type", "unknown")
            mtext = msg.text() if callable(getattr(msg, "text", None)) else str(msg)
            with open(console_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{mtype}] {mtext}\n")
        except Exception as e:
            with open(console_log_path, "a", encoding="utf-8") as f:
                f.write(f"[handler-error] {e}\n")

    page.on("console", on_console)
    # ------------------------------------------------------

    try:
        print("▶ ページにアクセス中...")
        page.goto(BASE_URL, timeout=30000)

        try:
            page.wait_for_load_state("networkidle", timeout=120000)
            print("通過したよ")
        except Exception:
            page.wait_for_load_state("domcontentloaded")
        print("🌐 到達URL:", page.url)

        # ポップアップ処理
        if POPUP_MODE and POPUP_BUTTONS:
            for i, label in enumerate(POPUP_BUTTONS, start=1):
                handled = click_button_in_order(page, label, step_idx=i, timeout_ms=BUTTON_TIMEOUT_MS)
                if handled:
                    page.wait_for_timeout(WAIT_BETWEEN_POPUPS_MS)
                else:
                    break
        else:
            print("ℹ ポップアップ処理はスキップしました")

        # 役割/認証スナップショット
        try:
            role_info = page.evaluate("() => ({ls: {...localStorage}, ck: document.cookie})")
            print("role/localStorage snapshot:", role_info)
        except Exception as e:
            print("role snapshot failed:", e)

        # 遅延読み込み対策のスクロール
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, 0)")
        except Exception as e:
            print("scroll nudge failed:", e)

        page.wait_for_load_state("load", timeout=30000)

        # 主要要素待ち（固定長ウェイトはしない）
        page.wait_for_selector(SELECTOR_TITLE, state="attached", timeout=30000)

        print("▶ 記事を抽出しています...")
        items = extract_items(
            page,
            SELECTOR_DATE,
            SELECTOR_TITLE,
            title_selector,
            title_index,
            href_selector,
            href_index,
            BASE_URL,
            date_selector,
            date_index,
            date_format,
            date_regex,
        )

        if not items:
            print("⚠ 抽出できた記事がありません。HTML構造が変わっている可能性があります。")

        os.makedirs("rss_output", exist_ok=True)
        rss_path = "rss_output/Feed20-2.xml"
        generate_rss(items, rss_path, BASE_URL, GAKKAI)
        print("✅ RSS 生成:", rss_path)

    except PlaywrightTimeoutError:
        print("⚠ ページの読み込みに失敗しました。")
        # 落ちる前に証跡保存
        save_dir = os.getcwd()
        html_path = os.path.join(save_dir, "page.html")
        screenshot_path = os.path.join(save_dir, "screenshot.png")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path=screenshot_path, full_page=True)
            print("💾 保存:", html_path, screenshot_path)
        except Exception as e:
            print("証跡保存に失敗:", e)
        raise
    except Exception as e:
        print("❗ 予期せぬエラー:", e)
        # 証跡保存
        save_dir = os.getcwd()
        html_path = os.path.join(save_dir, "page.html")
        screenshot_path = os.path.join(save_dir, "screenshot.png")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            page.screenshot(path=screenshot_path, full_page=True)
            print("💾 保存:", html_path, screenshot_path)
        except Exception as ee:
            print("証跡保存に失敗:", ee)
        raise
    finally:
        browser.close()
