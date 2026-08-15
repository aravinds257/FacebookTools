import os
import sys
import re
import asyncio
import urllib.parse
import urllib.request
from playwright.async_api import async_playwright

# Maximum budget cutoff in GBP
MAX_PRICE_GBP = 1200

# Try importing modern Google GenAI SDK or legacy SDK
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


def send_pushover(title: str, message: str):
    """Sends a push notification to your phone via Pushover API."""
    token = os.environ.get("PUSHOVER_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    if not token or not user:
        return
    
    # Pushover has a 1024 character limit, so we truncate if necessary
    truncated_msg = message[:1024] if len(message) > 1024 else message
    
    url = "https://api.pushover.net/1/messages.json"
    data = urllib.parse.urlencode({
        "token": token,
        "user": user,
        "title": title,
        "message": truncated_msg
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=data)
    try:
        urllib.request.urlopen(req)
        print("[Pushover] Deal notification sent to your phone!", flush=True)
    except Exception as e:
        print(f"[Pushover] Failed to send notification: {e}", flush=True)


async def fetch_marketplace_items(search_query: str = "gaming pc DDR5", location_slug: str = "london", radius_km: int = 40, max_price: int = 1200) -> str:
    """Automates Playwright browser to fetch Local PC listings from Gumtree UK under max_price GBP.
    Gumtree is used because it allows 24/7 automated scraping from cloud VMs (like Oracle) without login walls,
    while still supporting local face-to-face viewing and cash negotiation.
    """
    encoded_query = urllib.parse.quote(search_query)
    
    # Gumtree URL for Desktop Workstations/PCs
    url = f"https://www.gumtree.com/search?search_category=desktop-workstation-pcs&search_location={location_slug}&q={encoded_query}&max_price={max_price}&distance={radius_km}"

    print(f"[1/5] Launching Chromium browser (Max Budget: £{max_price})...", flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--lang=en-GB,en"
            ]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-GB"
        )
        page = await context.new_page()

        print(f"[2/5] Navigating to UK Local Classifieds (Gumtree):\n      Coverage Area='{location_slug} ({radius_km} miles)' | Query='{search_query}' | Max Price=£{max_price}\n      URL: {url}", flush=True)
        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
        except Exception:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            except Exception as e:
                print(f"[Scraper Warning] Initial page load notice: {e}", flush=True)

        # Handle UK / EU Cookie Consent Dialogs on Gumtree
        print("[3/5] Handling cookie consent...", flush=True)
        try:
            btn = page.locator("button#onetrust-accept-btn-handler").first
            if await btn.count() > 0 and await btn.is_visible():
                print("      Clicking Gumtree cookie consent button...", flush=True)
                await btn.click(timeout=3000)
                await page.wait_for_timeout(1500)
        except Exception as e:
            pass

        # Scroll down slightly to trigger lazy loading
        try:
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1000)
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        # JS Filter: Extract Gumtree Search Result Articles
        js_code = """
        () => {
            const listings = [];
            document.querySelectorAll('article[data-q="search-result"]').forEach(item => {
                const titleEl = item.querySelector('div[data-q="tile-title"]');
                const priceEl = item.querySelector('div[data-q="tile-price"]');
                const locEl = item.querySelector('div[data-q="tile-location"]');
                const linkEl = item.querySelector('a');
                
                if(titleEl && priceEl && linkEl) {
                    const title = titleEl.innerText.trim();
                    const price = priceEl.innerText.trim();
                    const loc = locEl ? locEl.innerText.trim() : "Unknown";
                    const url = linkEl.href;
                    listings.push(`[GUMTREE] Listing: ${price} | ${title} | ${loc}\\nURL: ${url}`);
                }
            });
            return listings.join("\\n--- New Listing ---\\n");
        }
        """

        listings_text = await page.evaluate(js_code)
        await browser.close()
        
        if not listings_text:
            print("[Warning] No structured listings found on Gumtree.", flush=True)
            return ""
            
        print(f"[4/5] Extracted {len(listings_text)} characters of STRICT UK Local listings (Filtered <= £{max_price}).", flush=True)
        return listings_text[:12000]


def analyze_with_gemini(raw_listings: str, api_key: str, search_query: str, location_slug: str, radius_km: int, max_price: int = 1200) -> str:
    """Uses Google Gemini API to appraise UK full Gaming PC deals with DDR5, PCIe 5.0, and PCSpecialist brand new price comparisons."""
    system_instruction = f"""You are an expert UK PC Hardware Appraisal Agent evaluating DESKTOP PCs (Focus on Upgradeable Foundation) in '{location_slug}', UK ({radius_km} miles radius).
STRICT BUDGET CONDITION: ALL LISTINGS EVALUATED MUST BE PRICED AT OR BELOW £{max_price} GBP. DISCARD ANY LISTING OVER £{max_price} GBP.
STRICT LOCATION REQUIREMENT: All listings evaluated MUST be located in the UK in Pounds (£). Ignore any US listings.

Your goal is to find DESKTOP PCs ON SALE (UNDER £{max_price}) that fulfill KEY FUTURE-PROOF REQUIREMENTS:
1. **DDR5 RAM (MANDATORY)**
2. **PCIe 5.0 Support / Latest Motherboards (HIGH PRIORITY)**: Look for AM5 (B650, X670, B850, X870) or Intel LGA1700/1851 (Z690, Z790, B760). The focus is on finding a system with an excellent motherboard foundation that allows for future RAM and GPU upgrades.
3. **GPU Requirement is Relaxed**: The user DOES NOT need a high-end gaming GPU right now. Basic GPUs or integrated graphics are perfectly fine as long as the Motherboard + CPU + DDR5 foundation is modern and highly upgradeable.

4. **PCSPECIALIST PRICE COMPARISON (CRITICAL)**:
   For every deal found, calculate the estimated brand-new equivalent build price if configured on **PCSpecialist (or Amazon UK)**, and calculate the **Savings (£)** vs buying new from PCSpecialist.

5. Output a structured Markdown table of the TOP DEALS UNDER £{max_price}:
   | Marketplace Price (£) | Complete Specs & Motherboard (Focus on Upgradeability) | Location | Estimated PCSpecialist Brand New Price (£) | Your Savings (£) vs PCSpecialist | Value Rating /10 | Listing Link |
"""

    prompt = f"{system_instruction}\n\nHere is the raw text dump from Local Classifieds (Gumtree) listings:\n\n{raw_listings}\n\nPlease evaluate these listings and provide your top full UK PC deal recommendations under £{max_price} with PCSpecialist price comparisons."

    if USE_SDK == "google-genai":
        client = genai.Client(api_key=api_key)
        
        active_models = []
        try:
            for m in client.models.list():
                model_name = m.name.replace("models/", "")
                if "flash" in model_name or "gemini" in model_name:
                    active_models.append(model_name)
        except Exception:
            pass

        if not active_models:
            active_models = ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]

        last_err = None
        for m in active_models:
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=prompt
                )
                print(f"[AI Agent] Successfully analyzed using model: '{m}'", flush=True)
                return response.text
            except Exception as e:
                last_err = e
                continue
        raise last_err

    elif USE_SDK == "google-generativeai":
        genai_legacy.configure(api_key=api_key)
        active_models = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-pro"]
        last_err = None
        for m in active_models:
            try:
                model = genai_legacy.GenerativeModel(model_name=m)
                response = model.generate_content(prompt)
                print(f"[AI Agent] Successfully analyzed using model: '{m}'", flush=True)
                return response.text
            except Exception as e:
                last_err = e
                continue
        raise last_err

    else:
        raise ImportError("No Google GenAI SDK installed. Please run: pip install google-genai")


async def run_search(location: str, query: str, radius: int, api_key: str, max_price: int = 1200):
    print(f"\n==================================================", flush=True)
    print(f" SEARCHING GUMTREE UK PCs (MAX £{max_price}): '{query}' | COVERAGE: {location} ({radius} miles)", flush=True)
    print(f"==================================================", flush=True)

    raw_listings = await fetch_marketplace_items(search_query=query, location_slug=location, radius_km=radius, max_price=max_price)

    new_listings = filter_new_listings(raw_listings)

    if not new_listings or len(new_listings.strip()) < 50:
        print(f"[Notice] No NEW UK listings found under £{max_price} since the last run. Skipping AI analysis.", flush=True)
        return

    print(f"[5/5] Analyzing {len(new_listings.split('--- New Listing ---'))} NEW listings using Google Gemini...", flush=True)
    try:
        analysis = analyze_with_gemini(new_listings, api_key, search_query=query, location_slug=location, radius_km=radius, max_price=max_price)
        print(f"\n=== GEMINI GUMTREE UK DEAL APPRAISAL (MAX £{max_price}) ===", flush=True)
        print(analysis, flush=True)
        
        # Send Pushover notification if credentials are provided
        send_pushover(f"New PC Deals Under £{max_price}", analysis)
        
    except Exception as e:
        print(f"[Error] Gemini API evaluation failed: {e}", flush=True)


async def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("\n" + "=" * 60)
        print("CRITICAL ERROR: 'GEMINI_API_KEY' environment variable is missing.")
        print("Please get a free API key from https://aistudio.google.com/")
        print("Then run: export GEMINI_API_KEY='your-key-here'")
        print("=" * 60 + "\n", flush=True)
        return

    max_price = int(os.environ.get("MAX_PRICE", "1200"))
    query = os.environ.get("SEARCH_QUERY", "PC DDR5")
    
    if len(sys.argv) >= 4:
        max_price = int(sys.argv[3])
    
    # Search Local area (40 miles radius) under max_price
    await run_search("london", query, 40, api_key, max_price=max_price)


if __name__ == "__main__":
    asyncio.run(main())
