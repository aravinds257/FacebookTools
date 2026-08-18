import os
import sys
import re
import json
import asyncio
import datetime
import subprocess
import urllib.parse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from playwright.async_api import async_playwright

MAX_PRICE_GBP = 1200

try:
    from google import genai
    USE_SDK = "google-genai"
except ImportError:
    try:
        import google.generativeai as genai_legacy
        USE_SDK = "google-generativeai"
    except ImportError:
        USE_SDK = None


def filter_new_listings(raw_listings: str) -> str:
    """Filters out listings that have already been seen in previous runs."""
    seen_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_urls.txt")
    
    if not os.path.exists(seen_file):
        open(seen_file, 'w').close()
        
    with open(seen_file, 'r') as f:
        seen_urls = set(f.read().splitlines())
        
    new_urls = []
    filtered_listings = []
    
    blocks = raw_listings.split("--- New Listing ---")
    for block in blocks:
        block = block.strip()
        if not block: continue
        
        match = re.search(r'URL:\s*(https?://\S+)', block)
        if match:
            url = match.group(1)
            # Remove query parameters from URL to avoid duplicates from tracking tags
            clean_url = url.split('?')[0] if '?' in url else url
            if clean_url not in seen_urls:
                filtered_listings.append(block)
                new_urls.append(clean_url)
                
    if new_urls:
        with open(seen_file, 'a') as f:
            for u in new_urls:
                f.write(u + '\n')
                
    return "\n--- New Listing ---\n".join(filtered_listings)


def parse_gemini_to_json(analysis_text: str, max_price: int) -> list:
    """Parses Gemini's markdown table output into a list of deal JSON objects for the web dashboard."""
    deals = []
    lines = analysis_text.split('\n')
    header_found = False
    uid = 1

    for line in lines:
        line = line.strip()
        # Skip separators and header rows
        if not line.startswith('|') or '---' in line:
            continue
        if 'Marketplace Price' in line or 'Price (£)' in line:
            header_found = True
            continue
        if not header_found:
            continue

        # Split and strip table cells
        cells = [c.strip() for c in line.split('|') if c.strip()]
        if len(cells) < 5:
            continue

        try:
            # Extract marketplace price (first number found)
            price_raw = re.sub(r'[^0-9]', '', cells[0].split()[0])
            if not price_raw:
                continue
            price = int(price_raw)
            if price > max_price or price < 100:
                continue

            specs = cells[1] if len(cells) > 1 else "Unknown Specs"
            location = cells[2] if len(cells) > 2 else "UK Local"
            
            # PCSpecialist price
            new_price_raw = re.sub(r'[^0-9]', '', cells[3].split()[0]) if len(cells) > 3 else ""
            new_price = int(new_price_raw) if new_price_raw else price + 400
            savings = max(new_price - price, 0)

            # Rating
            rating_raw = re.findall(r'(\d+\.?\d*)', cells[4] if len(cells) > 4 else "")
            rating = float(rating_raw[0]) if rating_raw else 7.0

            # URL (last cell)
            url_match = re.search(r'https?://\S+', cells[-1] if len(cells) > 5 else "")
            url = url_match.group(0).rstrip(')') if url_match else "#"

            # Gemini Verdict — second-to-last cell (before link)
            verdict = ""
            if len(cells) >= 3:
                # Verdict is the cell just before the last URL cell
                verdict_cell = cells[-2] if len(cells) > 6 else cells[-1]
                # Clean up any markdown link syntax
                verdict = re.sub(r'\[.*?\]\(.*?\)', '', verdict_cell).strip()
                verdict = verdict[:300]  # Cap length

            # Platform detection
            platform = "facebook" if "facebook" in url else "gumtree"

            # Socket detection from specs
            specs_lower = specs.lower()
            if any(x in specs_lower for x in ['am5', 'x670', 'b650', 'x870', 'b850', 'ryzen 7', 'ryzen 9', 'ryzen 5 7', 'ryzen 5 9']):
                socket = "am5"
            elif any(x in specs_lower for x in ['z790', 'z690', 'b760', 'i7-13', 'i9-13', 'i7-14', 'i9-14', 'lga1700', 'lga1851']):
                socket = "intel"
            else:
                socket = "am5"  # Default assumption for DDR5

            deals.append({
                "id": str(uid),
                "title": specs[:120],
                "price": price,
                "newPrice": new_price,
                "savings": savings,
                "rating": round(rating, 1),
                "platform": platform,
                "socket": socket,
                "specs": specs[:120],
                "motherboard": "AMD AM5 (Upgradeable 2027+)" if socket == "am5" else "Intel Z790 (PCIe 5.0)",
                "location": location,
                "verdict": verdict,
                "date_added": datetime.datetime.utcnow().strftime("%Y-%m-%d"),
                "url": url
            })
            uid += 1
        except Exception:
            continue

    return deals


def save_and_push_deals(deals: list):
    """Saves deals to docs/deals.json and auto-pushes to GitHub to update the live web dashboard."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    docs_dir = os.path.join(repo_root, "docs")
    json_path = os.path.join(docs_dir, "deals.json")

    if not os.path.exists(docs_dir):
        os.makedirs(docs_dir)

    # Load existing deals, merge in new ones (avoid duplicates by URL)
    existing = []
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing_urls = {d.get('url', '') for d in existing}
    new_deals = [d for d in deals if d.get('url', '') not in existing_urls]

    if not new_deals:
        print("[Dashboard] No new deals to add to the web dashboard.", flush=True)
        return

    merged = new_deals + existing  # New deals appear first
    merged = merged[:50]  # Keep max 50 deals on the dashboard

    # Add a last_updated timestamp
    for d in merged:
        if 'last_updated' not in d:
            d['last_updated'] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    with open(json_path, 'w') as f:
        json.dump(merged, f, indent=2)

    print(f"[Dashboard] Saved {len(new_deals)} new deals to docs/deals.json ({len(merged)} total).", flush=True)

    # Auto-commit and push to GitHub
    try:
        subprocess.run(["git", "-C", repo_root, "add", "docs/deals.json"], check=True, capture_output=True)
        commit_msg = f"Auto-update deals: {len(new_deals)} new ({datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')})"
        subprocess.run(["git", "-C", repo_root, "commit", "-m", commit_msg], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo_root, "push"], check=True, capture_output=True)
        print("[Dashboard] Successfully pushed updated deals.json to GitHub! 🚀", flush=True)
        print("[Dashboard] Live at: https://aravinds257.github.io/FacebookTools/", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"[Dashboard] Git push failed: {e.stderr.decode() if e.stderr else str(e)}", flush=True)

    return len(new_deals)  # Return count so caller knows if truly new deals were found


NOTIFICATION_EMAIL = "aravinds.257@gmail.com"


def send_email_notification(new_deal_count: int, deals: list, max_price: int):
    """Sends a rich HTML email to aravinds.257@gmail.com listing new PC deals found."""
    smtp_user = os.environ.get("SMTP_EMAIL")
    smtp_pass = os.environ.get("SMTP_PASSWORD")
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    subject = f"🖥️ PC Radar: {new_deal_count} New Deal{'s' if new_deal_count > 1 else ''} Found Under £{max_price:,}"

    # ── Plain text fallback
    text_lines = [
        f"PC Radar found {new_deal_count} new PC deal(s) under £{max_price:,}!",
        f"View live dashboard: https://aravinds257.github.io/FacebookTools/",
        "=" * 60, ""
    ]
    for i, d in enumerate(deals[:new_deal_count], 1):
        text_lines.append(f"{i}. {d.get('title', 'PC Deal')}")
        text_lines.append(f"   Price: £{d.get('price', 0):,} | Rating: {d.get('rating', 'N/A')}/10")
        if d.get('verdict'):
            text_lines.append(f"   Gemini: {d['verdict']}")
        text_lines.append(f"   Link: {d.get('url', '#')}")
        text_lines.append("-" * 40)
    text_body = "\n".join(text_lines)

    # ── Rich HTML email
    cards_html = []
    for d in deals[:new_deal_count]:
        savings = d.get('savings', 0)
        verdict_tag = ""
        if d.get('verdict'):
            verdict_tag = f"""
            <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px 14px;margin:12px 0;font-size:13px;color:#166534;line-height:1.4;">
                <strong style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;">🤖 Gemini Appraisal:</strong><br/>{d['verdict']}
            </div>"""
        saving_str = f'<span style="color:#16a34a;font-size:14px;font-weight:700;margin-left:8px;">💰 £{savings:,} under new price</span>' if savings > 0 else ""
        new_price_str = f'<div style="font-size:12px;color:#94a3b8;margin-top:4px;">vs PCSpecialist: <span style="text-decoration:line-through;">£{d.get("newPrice", 0):,}</span></div>' if d.get('newPrice') else ""
        cards_html.append(f"""
        <div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 4px rgba(0,0,0,0.05);">
            <div style="display:flex;justify-content:space-between;margin-bottom:10px;">
                <span style="background:#dbeafe;color:#1d4ed8;font-weight:700;font-size:12px;padding:3px 10px;border-radius:6px;">{(d.get('socket') or 'DDR5').upper()}</span>
                <span style="background:#fef3c7;color:#b45309;font-weight:800;font-size:12px;padding:3px 10px;border-radius:6px;">★ {d.get('rating', 7.5)} / 10</span>
            </div>
            <h3 style="margin:8px 0 4px;font-size:16px;color:#0f172a;font-weight:700;">{d.get('title', 'PC Deal')[:100]}</h3>
            <p style="margin:0 0 12px;font-size:13px;color:#64748b;">📍 {d.get('location', 'UK Local')}</p>
            <div style="background:#f8fafc;border-radius:8px;padding:12px;margin-bottom:12px;">
                <div style="font-size:22px;font-weight:800;color:#0f172a;">£{d.get('price', 0):,} {saving_str}</div>
                {new_price_str}
            </div>
            {verdict_tag}
            <a href="{d.get('url', '#')}" target="_blank"
               style="display:block;background:#1d4ed8;color:#fff;font-weight:700;font-size:14px;text-align:center;padding:11px;border-radius:8px;text-decoration:none;">
                View Listing &rarr;
            </a>
        </div>""")

    html_body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/></head>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f1f5f9;margin:0;padding:24px 12px;color:#334155;">
        <div style="max-width:600px;margin:0 auto;">
            <div style="background:linear-gradient(135deg,#1e3a8a,#1d4ed8);border-radius:12px;padding:24px;color:#fff;text-align:center;margin-bottom:24px;">
                <h1 style="margin:0 0 6px;font-size:22px;font-weight:800;">🖥️ PC Radar Deal Alert</h1>
                <p style="margin:0;font-size:14px;opacity:0.9;">{new_deal_count} new PC deal{'s' if new_deal_count > 1 else ''} found under £{max_price:,}</p>
            </div>
            {''.join(cards_html)}
            <div style="text-align:center;margin-top:24px;font-size:13px;color:#64748b;">
                <a href="https://aravinds257.github.io/FacebookTools/" style="color:#1d4ed8;font-weight:700;">View Full Dashboard &rarr;</a>
                <p style="font-size:11px;color:#94a3b8;margin-top:8px;">Powered by Playwright · Google Gemini AI</p>
            </div>
        </div>
    </body></html>"""

    # If SMTP not configured, print links to terminal instead
    if not smtp_user or not smtp_pass:
        print(f"[Email] SMTP_EMAIL / SMTP_PASSWORD not set — printing deals to terminal instead.", flush=True)
        print(f"[Email] To enable emails: add SMTP_EMAIL and SMTP_PASSWORD to your environment.", flush=True)
        for d in deals[:new_deal_count]:
            print(f"  💻 £{d.get('price', 0):,} | {d.get('title', '')[:60]} → {d.get('url', '#')}", flush=True)
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"PC Radar <{smtp_user}>"
        msg["To"]      = NOTIFICATION_EMAIL
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html",  "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [NOTIFICATION_EMAIL], msg.as_string())

        print(f"[Email] Notification sent to {NOTIFICATION_EMAIL} with {new_deal_count} deal(s)! 📧", flush=True)
    except Exception as e:
        print(f"[Email] Failed to send: {e}", flush=True)


async def fetch_gumtree_items(page, search_query: str, location_slug: str, radius_km: int, max_price: int) -> str:
    encoded_query = urllib.parse.quote(search_query)
    url = f"https://www.gumtree.com/search?search_category=desktop-workstation-pcs&search_location={location_slug}&q={encoded_query}&max_price={max_price}&distance={radius_km}"

    print(f"[Gumtree] Navigating to: {url}", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        print(f"[Gumtree Warning] {e}", flush=True)

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

    js_code = """
    () => {
        const listings = [];
        document.querySelectorAll('article').forEach(item => {
            const titleEl = item.querySelector('[data-q="tile-title"]') || item.querySelector('h2') || item.querySelector('h3');
            const priceEl = item.querySelector('[data-q="tile-price"]') || item.querySelector('span[data-q="price"]');
            const locEl = item.querySelector('[data-q="tile-location"]');
            const linkEl = item.querySelector('a');
            
            if(linkEl && linkEl.href && (titleEl || priceEl)) {
                const title = titleEl ? titleEl.innerText.trim() : "Custom PC";
                const price = priceEl ? priceEl.innerText.trim() : "£0";
                const loc = locEl ? locEl.innerText.trim() : "UK Local";
                const url = linkEl.href;
                listings.push(`[GUMTREE] Listing: ${price} | ${title} | ${loc}\\nURL: ${url}`);
            }
        });
        return listings.join("\\n--- New Listing ---\\n");
    }
    """
    return await page.evaluate(js_code)


async def fetch_facebook_items(page, search_query: str, location_slug: str, radius_km: int, max_price: int) -> str:
    encoded_query = urllib.parse.quote(search_query)
    # London Loc ID
    london_loc_id = "108096742551526"
    url = f"https://www.facebook.com/marketplace/london/search/?query={encoded_query}&location_id={london_loc_id}&radius={radius_km}&exact=true"

    print(f"[Facebook] Navigating to: {url}", flush=True)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except Exception as e:
        print(f"[Facebook Warning] {e}", flush=True)

    try:
        for selector in ['button[aria-label*="Allow"]', 'div[aria-label*="Allow"]', 'button:has-text("Allow")']:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=3000)
                await page.wait_for_timeout(1500)
                break
    except Exception:
        pass

    try:
        await page.evaluate("window.scrollBy(0, 1200)")
        await page.wait_for_timeout(2000)
        await page.evaluate("window.scrollBy(0, 1200)")
        await page.wait_for_timeout(2000)
    except Exception:
        pass

    js_code = """
    (maxPrice) => {
        const itemLinks = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
        if (itemLinks.length > 0) {
            const uniqueListings = new Map();
            itemLinks.forEach(a => {
                const text = a.innerText ? a.innerText.trim() : '';
                const href = a.href;
                const isUS = text.includes('$') || text.includes(', CA') || text.includes('California');
                if (text && text.length > 5 && !uniqueListings.has(href) && !isUS) {
                    if (text.includes('£') || text.includes('Free') || text.includes('Kingdom') || text.includes('London')) {
                        const match = text.match(/£([0-9,]+)/);
                        let price = 0;
                        if (match) {
                            price = parseInt(match[1].replace(/,/g, ''), 10);
                        }
                        if (price === 0 || price <= maxPrice) {
                            uniqueListings.set(href, "[FACEBOOK] Listing: " + text.replace(/\\n+/g, ' | ') + "\\nURL: " + href);
                        }
                    }
                }
            });
            if (uniqueListings.size > 0) {
                return Array.from(uniqueListings.values()).join("\\n--- New Listing ---\\n");
            }
        }
        return ""; // Only return actual listings, avoid returning login dump if blocked
    }
    """
    return await page.evaluate(js_code, max_price)


async def fetch_both_marketplaces(search_query: str, max_price: int) -> str:
    targets = [
        {"slug": "london", "name": "London", "radius_km": 8, "distance_miles": 5},   # 5 miles
        {"slug": "woking", "name": "Woking", "radius_km": 32, "distance_miles": 20}, # 20 miles
    ]

    print(f"[1/5] Launching Chromium browser (Max Budget: £{max_price})...", flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        
        context_args = {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800},
            "locale": "en-GB",
            "geolocation": {"latitude": 51.5074, "longitude": -0.1278},
            "permissions": ["geolocation"]
        }

        # Check for state.json for Facebook Auth
        state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
        if os.path.exists(state_file):
            print(f"      [Auth] Loading saved session cookies from '{state_file}'...", flush=True)
            context_args["storage_state"] = state_file

        context = await browser.new_context(**context_args)
        page = await context.new_page()

        all_gumtree = []
        all_facebook = []

        for t in targets:
            print(f"[2/5] Fetching Gumtree UK: {t['name']} ({t['distance_miles']} miles)...", flush=True)
            g_text = await fetch_gumtree_items(page, search_query, t["slug"], t["distance_miles"], max_price)
            if g_text:
                all_gumtree.append(g_text)

            print(f"[3/5] Fetching Facebook Marketplace UK: {t['name']} ({t['radius_km']} km)...", flush=True)
            fb_text = await fetch_facebook_items(page, search_query, t["slug"], t["radius_km"], max_price)
            if fb_text:
                all_facebook.append(fb_text)

        await browser.close()
        
        combined_parts = []
        if all_gumtree:
            combined_parts.extend(all_gumtree)
        if all_facebook:
            combined_parts.extend(all_facebook)

        combined = "\n--- New Listing ---\n".join(combined_parts)
        print(f"[4/5] Extracted {len(combined)} characters across London (5 miles) & Woking (20 miles).", flush=True)
        return combined[:15000]


def analyze_with_gemini(raw_listings: str, api_key: str, search_query: str, max_price: int = 1200) -> str:
    system_instruction = f"""You are an expert UK PC Hardware Appraisal Agent evaluating DESKTOP PCs in London (5 miles radius) and Woking (20 miles radius), UK.
STRICT BUDGET CONDITION: ALL LISTINGS EVALUATED MUST BE PRICED AT OR BELOW £{max_price} GBP. DISCARD ANY LISTING OVER £{max_price} GBP.

Your goal is to find DESKTOP PCs ON SALE (UNDER £{max_price}) that fulfill KEY FUTURE-PROOF REQUIREMENTS:
1. **DDR5 RAM (MANDATORY)**
2. **PCIe 5.0 Support / Latest Motherboards (HIGH PRIORITY)**: Look for AM5 (B650, X670, B850, X870) or Intel LGA1700/1851 (Z690, Z790, B760). The focus is on finding a system with an excellent motherboard foundation that allows for future RAM and GPU upgrades.
3. **GPU Requirement is Relaxed**: The user DOES NOT need a high-end gaming GPU right now. Basic GPUs or integrated graphics are perfectly fine as long as the Motherboard + CPU + DDR5 foundation is modern and highly upgradeable.

4. **PCSPECIALIST PRICE COMPARISON (CRITICAL)**:
   For every deal found, calculate the estimated brand-new equivalent build price if configured on **PCSpecialist (or Amazon UK)**, and calculate the **Savings (£)**.

5. Output a structured Markdown table of the TOP DEALS UNDER £{max_price}. You MUST include ALL of these columns in this exact order:
   | Source | Marketplace Price (£) | Complete Specs & Motherboard (Focus on Upgradeability) | Location | Estimated PCSpecialist Brand New Price (£) | Your Savings (£) | Value Rating /10 | Gemini Verdict (1 sentence: WHY this score — mention socket, upgrade path, GPU value, and savings) | Listing Link |
"""

    prompt = f"{system_instruction}\n\nHere is the combined raw text dump from Gumtree and Facebook Marketplace listings in London (5km) and Woking (20km):\n\n{raw_listings}\n\nPlease evaluate these listings and provide your top full UK PC deal recommendations under £{max_price}."

    if USE_SDK == "google-genai":
        client = genai.Client(api_key=api_key)
        
        active_models = []
        try:
            for m in client.models.list():
                model_name = m.name.replace("models/", "")
                if "flash" in model_name or "gemini" in model_name:
                    active_models.append(model_name)
        except Exception: pass
        if not active_models: active_models = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]

        for m in active_models:
            try:
                response = client.models.generate_content(model=m, contents=prompt)
                print(f"[AI Agent] Successfully analyzed using model: '{m}'", flush=True)
                return response.text
            except Exception: continue
        raise Exception("Failed to call Gemini API")

    elif USE_SDK == "google-generativeai":
        genai_legacy.configure(api_key=api_key)
        for m in ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]:
            try:
                model = genai_legacy.GenerativeModel(model_name=m)
                response = model.generate_content(prompt)
                print(f"[AI Agent] Successfully analyzed using model: '{m}'", flush=True)
                return response.text
            except Exception: continue
        raise Exception("Failed to call Gemini API")
    else:
        raise ImportError("No Google GenAI SDK installed.")


async def run_search(query: str, api_key: str, max_price: int = 1200):
    print(f"\n==================================================", flush=True)
    print(f" SEARCHING GUMTREE + FACEBOOK UK PCs (MAX £{max_price}): '{query}' | London (5 miles) + Woking (20 miles)", flush=True)
    print(f"==================================================", flush=True)

    raw_listings = await fetch_both_marketplaces(search_query=query, max_price=max_price)

    new_listings = filter_new_listings(raw_listings)

    if not new_listings or len(new_listings.strip()) < 50:
        print(f"[Notice] No NEW UK listings found under £{max_price} in London (5 miles) or Woking (20 miles). Skipping AI analysis.", flush=True)
        return

    print(f"[5/5] Analyzing {len(new_listings.split('--- New Listing ---'))} NEW combined listings using Google Gemini...", flush=True)
    try:
        analysis = analyze_with_gemini(new_listings, api_key, search_query=query, max_price=max_price)
        print(f"\n=== GEMINI COMBINED UK DEAL APPRAISAL (MAX £{max_price}) ===", flush=True)
        print(analysis, flush=True)
        
        # Parse results and auto-update the GitHub Pages web dashboard
        deals = parse_gemini_to_json(analysis, max_price)
        new_deal_count = 0
        if deals:
            new_deal_count = save_and_push_deals(deals) or 0

        # Only send email if genuinely NEW PCs were found (not seen before)
        if new_deal_count > 0:
            send_email_notification(new_deal_count, deals, max_price)
        else:
            print("[Email] No new unique deals — notification skipped.", flush=True)

    except Exception as e:
        print(f"[Error] Gemini API evaluation failed: {e}", flush=True)


async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("CRITICAL ERROR: 'GEMINI_API_KEY' environment variable is missing.", flush=True)
        return

    max_price = int(os.environ.get("MAX_PRICE", "1500"))
    query = os.environ.get("SEARCH_QUERY", "PC DDR5")
    
    if len(sys.argv) >= 4:
        max_price = int(sys.argv[3])
    
    await run_search(query, api_key, max_price=max_price)

if __name__ == "__main__":
    asyncio.run(main())