import os
import json
import re
import asyncio
import datetime
import subprocess
import smtplib
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from playwright.async_api import async_playwright

# -- Load .env file if present (optional dependency)
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv not installed; fall back to system env vars

# -- Constants
SCRIPT_DIR      = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT       = SCRIPT_DIR
TRACKED_FILE    = os.path.join(SCRIPT_DIR, "tracked_prices.json")
REDUCTIONS_FILE = os.path.join(SCRIPT_DIR, "docs", "reductions.json")
NOTIFICATION_EMAIL = "aravinds.257@gmail.com"

# -- JS extractor shared between search pages
_EXTRACT_JS = r"""
() => {
    const results = [];
    const seen = new Set();
    document.querySelectorAll('a[href*="/p/"]').forEach(a => {
        const href = a.href ? a.href.split('?')[0] : '';
        if (!href.includes('/p/') || seen.has(href)) return;
        const text = a.innerText || '';
        const priceMatch = text.match(/\u00a3([0-9,]+)/);
        if (priceMatch) {
            const price = parseInt(priceMatch[1].replace(/,/g,''), 10);
            if (price >= 100 && price <= 3000) {
                seen.add(href);
                results.push({url: href, price: price, title: text.split('\n')[0].trim().substring(0,120)});
            }
        }
    });
    return results;
}
"""


def load_tracked() -> dict:
    if os.path.exists(TRACKED_FILE):
        try:
            with open(TRACKED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_tracked(data: dict):
    with open(TRACKED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_reductions() -> list:
    if os.path.exists(REDUCTIONS_FILE):
        try:
            with open(REDUCTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_reductions(data: list):
    os.makedirs(os.path.dirname(REDUCTIONS_FILE), exist_ok=True)
    with open(REDUCTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


async def scrape_gumtree(page, label: str, url: str) -> list:
    print(f"[PriceDrop] Scraping Gumtree {label}...", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
    except Exception as e:
        print(f"[PriceDrop] Warning navigating to {label}: {e}", flush=True)

    try:
        btn = page.locator("button#onetrust-accept-btn-handler").first
        if await btn.count() > 0 and await btn.is_visible():
            await btn.click(timeout=3000)
            await page.wait_for_timeout(1500)
    except Exception:
        pass

    try:
        await page.evaluate("window.scrollBy(0, 800)")
        await page.wait_for_timeout(2000)
    except Exception:
        pass

    try:
        return await page.evaluate(_EXTRACT_JS)
    except Exception as e:
        print(f"[PriceDrop] JS evaluation error on {label}: {e}", flush=True)
        return []


def process_listings(listings: list, tracked: dict, today: str) -> list:
    drops = []
    for item in listings:
        url   = item.get("url", "")
        price = item.get("price", 0)
        title = item.get("title", "Unknown")
        if not url or price <= 0:
            continue

        if url not in tracked:
            tracked[url] = {
                "title":           title,
                "first_price":     price,
                "last_price":      price,
                "date_first_seen": today,
                "last_seen":       today,
            }
        else:
            entry = tracked[url]
            last  = entry.get("last_price", price)
            entry["last_seen"] = today
            if title:
                entry["title"] = title

            if price < last:
                drop_amt = last - price
                drop_pct = round((drop_amt / last) * 100)
                print(
                    f"[PriceDrop] \U0001f53b {title}: \u00a3{last} \u2192 \u00a3{price} "
                    f"(\u00a3{drop_amt} off, {drop_pct}%)",
                    flush=True,
                )
                drops.append({
                    "title":        title,
                    "old_price":    last,
                    "new_price":    price,
                    "drop":         drop_amt,
                    "drop_pct":     drop_pct,
                    "date_dropped": today,
                    "url":          url,
                    "platform":     "gumtree",
                })
                entry["last_price"] = price

    return drops


def merge_reductions(existing: list, new_drops: list) -> list:
    seen_keys = {(d.get("url"), d.get("date_dropped")) for d in existing}
    for drop in new_drops:
        key = (drop.get("url"), drop.get("date_dropped"))
        if key not in seen_keys:
            existing.insert(0, drop)
            seen_keys.add(key)

    result = existing[:50]
    for i, entry in enumerate(result, 1):
        entry["id"] = str(i)
    return result


def git_commit_and_push(n: int, date: str):
    try:
        subprocess.run(
            ["git", "-C", REPO_ROOT, "add", "docs/reductions.json", "tracked_prices.json"],
            check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", REPO_ROOT, "commit", "-m",
             f"Price drops: {n} reduction(s) detected ({date})"],
            check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", REPO_ROOT, "push"],
            check=True, capture_output=True
        )
        print("[PriceDrop] Pushed to GitHub.", flush=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="ignore").strip() if e.stderr else str(e)
        print(f"[PriceDrop] Git push failed: {err}", flush=True)


def send_email_notification(drops: list):
    smtp_user = os.environ.get("SMTP_EMAIL")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    n = len(drops)
    subject = f"\U0001f53b PC Radar: {n} Price Drop(s) Detected!"

    lines = [f"PC Radar detected {n} price drop(s) on Gumtree!\n"]
    for d in drops:
        lines.append(
            f"\u2022 {d['title']}\n"
            f"  \u00a3{d['old_price']} \u2192 \u00a3{d['new_price']} "
            f"(\u00a3{d['drop']} off, {d['drop_pct']}%)\n"
            f"  {d['url']}\n"
        )
    lines.append("\nView dashboard: https://aravinds257.github.io/FacebookTools/")
    text_body = "\n".join(lines)

    if not smtp_user or not smtp_pass:
        print("[PriceDrop] SMTP not configured - printing drops to terminal.", flush=True)
        for d in drops:
            print(
                f"  \U0001f53b \u00a3{d['old_price']} \u2192 \u00a3{d['new_price']} | "
                f"{d['title'][:70]} \u2192 {d['url']}",
                flush=True,
            )
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"PC Radar <{smtp_user}>"
        msg["To"]      = NOTIFICATION_EMAIL
        msg.attach(MIMEText(text_body, "plain", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [NOTIFICATION_EMAIL], msg.as_string())

        print(f"[PriceDrop] Email sent to {NOTIFICATION_EMAIL} with {n} drop(s).", flush=True)
    except Exception as e:
        print(f"[PriceDrop] Email send failed: {e}", flush=True)


async def main():
    max_price = int(os.environ.get("MAX_PRICE", "1500"))

    search_urls = [
        (
            "London",
            f"https://www.gumtree.com/search?search_category=desktop-workstation-pcs"
            f"&search_location=london&q=PC%20DDR5&max_price={max_price}&distance=5&sortType=2",
        ),
        (
            "Woking",
            f"https://www.gumtree.com/search?search_category=desktop-workstation-pcs"
            f"&search_location=woking&q=PC%20DDR5&max_price={max_price}&distance=20&sortType=2",
        ),
    ]

    tracked = load_tracked()
    today   = datetime.date.today().isoformat()
    all_listings: list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
            geolocation={"latitude": 51.5074, "longitude": -0.1278},
            permissions=["geolocation"],
        )
        page = await context.new_page()

        for label, url in search_urls:
            items = await scrape_gumtree(page, label, url)
            all_listings.extend(items)

        await browser.close()

    # Deduplicate by URL across both search results
    seen_urls: set = set()
    unique_listings = []
    for item in all_listings:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique_listings.append(item)

    print(
        f"[PriceDrop] Tracking {len(tracked)} known listing(s). "
        f"Found {len(unique_listings)} listings this run.",
        flush=True,
    )

    new_drops = process_listings(unique_listings, tracked, today)
    save_tracked(tracked)

    if new_drops:
        existing = load_reductions()
        merged   = merge_reductions(existing, new_drops)
        save_reductions(merged)
        n = len(new_drops)
        print(f"[PriceDrop] {n} new price drop(s) saved.", flush=True)
        git_commit_and_push(n, today)
        send_email_notification(new_drops)
    else:
        print("[PriceDrop] No new price drops detected this run.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
