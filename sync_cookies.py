"""
Utility script to extract fresh YouTube cookies from local browser
and export them for Railway, GitHub Secrets, or Hugging Face.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path


def main() -> None:
    print("=" * 60)
    print("🍪 Local YouTube Cookie Exporter & Sync Tool")
    print("=" * 60)

    try:
        import browser_cookie3
    except ImportError:
        print("[*] Installing browser_cookie3...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "browser_cookie3"])
        import browser_cookie3

    cookie_file = Path("youtube-cookies.txt")
    print("\n[*] Searching local browsers (Chrome, Edge, Firefox, Brave) for YouTube session...")

    cj = None
    for browser_fn in (
        browser_cookie3.chrome,
        browser_cookie3.edge,
        browser_cookie3.brave,
        browser_cookie3.firefox,
    ):
        try:
            cj = browser_fn(domain_name=".youtube.com")
            if any("youtube.com" in getattr(c, "domain", "") for c in cj):
                print(f"✅ Found YouTube cookies in {browser_fn.__name__}!")
                break
        except Exception:
            continue

    if not cj:
        print("❌ Could not extract cookies automatically. Ensure you are signed into YouTube in Chrome or Edge.")
        return

    # Write in Netscape format
    lines = ["# Netscape HTTP Cookie File\n"]
    for cookie in cj:
        if "youtube.com" in getattr(cookie, "domain", ""):
            domain = cookie.domain
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.path or "/"
            secure = "TRUE" if cookie.secure else "FALSE"
            expires = str(int(cookie.expires or 0))
            name = cookie.name
            value = cookie.value
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

    cookie_content = "".join(lines)
    cookie_file.write_text(cookie_content, encoding="utf-8")
    b64_content = base64.b64encode(cookie_content.encode("utf-8")).decode("utf-8")

    print(f"\n✅ Exported {len(lines) - 1} cookies to {cookie_file.resolve()}")
    print("\n📋 BASE64 ENCODED STRING (for GitHub Secrets / Railway Variables):")
    print("-" * 60)
    print(b64_content)
    print("-" * 60)
    print("\n💡 Copy the string above and set it as:")
    print("   - In Railway: YOUTUBE_COOKIES_CONTENT")
    print("   - In GitHub Secrets: YOUTUBE_COOKIES_TXT")


if __name__ == "__main__":
    main()
