import asyncio
from playwright.async_api import async_playwright

async def generate_state():
    print("Capturing session state & cookies from local browser...", flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-GB"
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com/marketplace/london/search/?query=DDR5%20ram", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # Dismiss cookie popup if present locally
        try:
            for selector in ['button:has-text("Allow")', 'button:has-text("Accept")', 'button[aria-label*="Allow"]']:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    break
        except Exception:
            pass

        # Save cookies & storage state to state.json
        await context.storage_state(path="state.json")
        print("Successfully saved session state to 'state.json'!", flush=True)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(generate_state())
