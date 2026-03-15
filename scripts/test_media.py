import asyncio
import sys
import os

# Add the project root to the path so we can import app
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.media_processor import fetch_media_from_snapshot

async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_media.py <snapshot_url> [ad_archive_id]")
        sys.exit(1)
        
    snapshot_url = sys.argv[1]
    ad_id = sys.argv[2] if len(sys.argv) > 2 else "test_ad_123"
    
    # Needs to run within an initialized context if media_processor uses configs
    # the config will load automatically from environment
    
    print(f"Testing media extraction for ad snapshot...")
    print(f"URL: {snapshot_url}")
    print(f"Ad ID: {ad_id}")
    print("-" * 50)
    
    result = await fetch_media_from_snapshot(snapshot_url, ad_id)
    
    print("\n--- RESULTS ---")
    if result:
        print("✅ SUCCESS: Media extracted!")
        print(f"Local Path: {result.get('media_local_path')}")
        
        frames = result.get('frame_paths')
        if frames:
            print(f"Extracted {len(frames)} frames for video analysis.")
    else:
        print("❌ FAILED: Could not extract media from the snapshot URL.")
        print("This could be due to the ad being taken down, a rate limit, or an unsupported media format.")

if __name__ == "__main__":
    asyncio.run(main())
