import asyncio
from playwright.async_api import async_playwright
from app.core.logging import get_logger

logger = get_logger(__name__)

async def fetch_media_urls_with_playwright(snapshot_url: str) -> tuple[str | None, str | None]:
    """
    Given a snapshot URL, launches Playwright to extract the actual video or image src.
    Returns (image_url, video_url).
    """
    logger.info("playwright_fetch_started", url=snapshot_url)
    image_url = None
    video_url = None
    
    try:
        async with async_playwright() as p:
            # Launch headless chromium with anti-bot/sandbox bypassing flags
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', 
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu'
                ]
            )
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Go to snapshot url, wait until the page has finished rendering DOM
            await page.goto(snapshot_url, wait_until="networkidle", timeout=45000)
            
            # Wait longer for React/scripts to fully populate `<video>` or `<img>` layouts
            await page.wait_for_timeout(5000)
            
            # 1. Try to find a video tag first (Video Ad)
            video_element = await page.query_selector("video")
            if video_element:
                video_url = await video_element.get_attribute("src")
            
            # 2. Extract image if video is not available or we need a poster/image
            if not video_url:
                # Based on DOM analysis, main ad images have referrerpolicy="origin-when-cross-origin"
                images = await page.query_selector_all("img[referrerpolicy='origin-when-cross-origin']")
                for img in images:
                    src = await img.get_attribute("src")
                    if src and ("scontent" in src or "fbcdn" in src):
                        image_url = src
                        break

                # Fallback: find the largest image on the screen (highest area)
                if not image_url:
                    images = await page.query_selector_all("img")
                    largest_area = 0
                    
                    for img in images:
                        src = await img.get_attribute("src")
                        if not src:
                            continue
                            
                        # Heuristic to only consider facebook CDNs
                        if "fbcdn" in src or "scontent" in src:
                            box = await img.bounding_box()
                            if box:
                                area = box["width"] * box["height"]
                                # Must be reasonably sized (ignore tiny 10x10 tracking pixels or icons)
                                if area > 20000 and area > largest_area:
                                    largest_area = area
                                    image_url = src

            await browser.close()
            
            logger.info("playwright_fetch_success", url=snapshot_url, video=video_url is not None, image=image_url is not None)
            return image_url, video_url
            
    except Exception as e:
        logger.error("playwright_fetch_failed", url=snapshot_url, error=str(e))
        return None, None
