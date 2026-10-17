import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
import httpx
from backend.config import settings

async def test_connection():
    print("=" * 65)
    print("WooCommerce REST API Connection Test")
    print("=" * 65)

    store_url = settings.WC_STORE_URL.rstrip("/")
    has_key = bool(settings.WC_CONSUMER_KEY and settings.WC_CONSUMER_KEY.strip())
    has_secret = bool(settings.WC_CONSUMER_SECRET and settings.WC_CONSUMER_SECRET.strip())

    print(f"Store URL Configured        : {store_url or 'None'}")
    print(f"Consumer Key Present        : {'YES (Configured)' if has_key else 'NO (Missing)'}")
    print(f"Consumer Secret Present     : {'YES (Configured)' if has_secret else 'NO (Missing)'}")
    print(f"Mock Mode Status            : {'Active (WC_MOCK_MODE=true)' if settings.WC_MOCK_MODE else 'Disabled (LIVE)'}")
    print("-" * 65)

    if not store_url or not has_key or not has_secret:
        print("[!] ERROR: Credentials incomplete. Please ensure WC_STORE_URL,")
        print("    WC_CONSUMER_KEY, and WC_CONSUMER_SECRET are populated in your .env file.")
        print("=" * 65)
        return False

    target_endpoint = f"{store_url}/wp-json/wc/v3/products"
    params = {"per_page": 1}

    print(f"[*] Testing authentication against: {target_endpoint}...")

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                target_endpoint,
                auth=(settings.WC_CONSUMER_KEY, settings.WC_CONSUMER_SECRET),
                params=params
            )

            print(f"[*] HTTP Response Status Code: {resp.status_code} {resp.reason_phrase}")

            if resp.status_code == 200:
                data = resp.json()
                total_products = resp.headers.get("x-wp-total", str(len(data)))
                print("[+] SUCCESS: WooCommerce REST API authenticated successfully!")
                print(f"[+] Total Products detected in store: {total_products}")
                print("=" * 65)
                return True
            elif resp.status_code in (401, 403):
                print(f"[-] AUTHENTICATION FAILED ({resp.status_code}): Invalid Consumer Key or Consumer Secret.")
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("message", "Authentication rejected by store.")
                    print(f"[-] WooCommerce Message: {err_msg}")
                except Exception:
                    pass
                print("=" * 65)
                return False
            elif resp.status_code == 404:
                print(f"[-] NOT FOUND (404): WooCommerce REST API endpoint not found at {target_endpoint}.")
                print("[-] Please verify that WooCommerce plugin is active and permalinks are enabled (e.g. /%postname%/).")
                print("=" * 65)
                return False
            else:
                print(f"[-] UNEXPECTED STATUS ({resp.status_code}): {resp.text[:200]}")
                print("=" * 65)
                return False
    except httpx.ConnectError as e:
        print(f"[-] CONNECTION FAILED: Unable to reach store host. Check DNS or network: {e}")
        print("=" * 65)
        return False
    except httpx.TimeoutException:
        print("[-] TIMEOUT: Store did not respond within 15 seconds.")
        print("=" * 65)
        return False
    except Exception as e:
        print(f"[-] ERROR: Unexpected error during test: {type(e).__name__}: {e}")
        print("=" * 65)
        return False

if __name__ == "__main__":
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)
