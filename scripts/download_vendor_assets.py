"""
download_vendor_assets.py
==========================
Run this ONCE, on a machine that has internet access, before you build the
final offline installer. It downloads every CDN-hosted UI asset this
application uses (Bootstrap 5 CSS/JS, Bootstrap Icons CSS + icon font
files, Chart.js, and the Google Fonts used by static/css/style.css) into
static/vendor/, so the packaged .exe never needs to reach the internet to
render the UI correctly.

WHY THIS IS A SEPARATE SCRIPT AND NOT DONE AUTOMATICALLY AT APP STARTUP:
the whole point of offline bundling is that the shipped .exe must NOT need
network access. A build-time download step (run by the developer/packager,
once, before `pyinstaller attendance_app.spec`) is the correct place for
this - not something the app tries to do on every customer's machine.

USAGE:
    pip install requests
    python scripts/download_vendor_assets.py

This writes into <repo_root>/static/vendor/. It is safe to re-run; it
always re-downloads and overwrites (there is no version-drift risk since
every URL below is pinned to the exact version already used by the
templates - see templates/*.html and static/css/style.css before this
refactor).

If you upgrade Bootstrap/Chart.js versions in the future: update the
version-pinned URLs in this file to match, re-run it, and the local
files under static/vendor/ are replaced with the new version - the
templates themselves reference version-agnostic local paths
(static/vendor/bootstrap/css/bootstrap.min.css etc.) so they do NOT need
any changes when you bump a vendor version here.

NETWORK ACCESS REQUIRED: this script needs internet access to run. It is
meant to be run by the developer building the release, not shipped to or
run by the end customer.
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    print("This script requires the 'requests' package.")
    print("Install it with:  pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "static" / "vendor"

BOOTSTRAP_CSS_DIR = VENDOR_DIR / "bootstrap" / "css"
BOOTSTRAP_JS_DIR = VENDOR_DIR / "bootstrap" / "js"
BOOTSTRAP_ICONS_DIR = VENDOR_DIR / "bootstrap-icons"
CHARTJS_DIR = VENDOR_DIR / "chartjs"
FONTS_DIR = VENDOR_DIR / "fonts"

# ---------------------------------------------------------------------
# Pinned versions - MUST match what templates/*.html previously loaded
# from jsdelivr, so swapping to local files changes nothing visually.
# ---------------------------------------------------------------------
BOOTSTRAP_VERSION = "5.3.0"
BOOTSTRAP_ICONS_VERSION = "1.10.0"

BOOTSTRAP_CSS_URL = f"https://cdn.jsdelivr.net/npm/bootstrap@{BOOTSTRAP_VERSION}/dist/css/bootstrap.min.css"
BOOTSTRAP_JS_URL = f"https://cdn.jsdelivr.net/npm/bootstrap@{BOOTSTRAP_VERSION}/dist/js/bootstrap.bundle.min.js"
BOOTSTRAP_ICONS_CSS_URL = f"https://cdn.jsdelivr.net/npm/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/bootstrap-icons.css"
# Base used to resolve the relative ./fonts/*.woff2 url() references inside
# the bootstrap-icons.css file fetched above.
BOOTSTRAP_ICONS_BASE = f"https://cdn.jsdelivr.net/npm/bootstrap-icons@{BOOTSTRAP_ICONS_VERSION}/font/"
# Unpinned upstream in the original templates (`.../npm/chart.js`, which
# jsdelivr resolves to whatever the current latest major/minor is). Pinned
# here to a known-good UMD build so a re-run of this script always produces
# byte-identical output rather than silently picking up a future breaking
# major version.
CHARTJS_VERSION = "4.4.4"
CHARTJS_URL = f"https://cdn.jsdelivr.net/npm/chart.js@{CHARTJS_VERSION}/dist/chart.umd.min.js"

GOOGLE_FONTS_CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Manrope:wght@500;600;700;800"
    "&family=Inter:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500;600"
    "&display=swap"
)
# A real browser User-Agent is required here: Google Fonts serves modern
# woff2 files only to UAs it recognizes as supporting them, and returns a
# legacy .ttf/eot @font-face block to unrecognized clients (e.g. Python's
# default requests UA) - woff2 is smaller and is what every current browser
# actually uses, so we spoof a modern desktop Chrome UA specifically to
# get the same CSS a real visitor's browser would receive.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _download(url, dest_path, headers=None):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {url}")
    resp = requests.get(url, headers=headers or {}, timeout=30)
    resp.raise_for_status()
    dest_path.write_bytes(resp.content)
    print(f"  -> saved to {dest_path.relative_to(REPO_ROOT)} ({len(resp.content):,} bytes)")
    return resp.content


def download_bootstrap():
    _download(BOOTSTRAP_CSS_URL, BOOTSTRAP_CSS_DIR / "bootstrap.min.css")
    _download(BOOTSTRAP_JS_URL, BOOTSTRAP_JS_DIR / "bootstrap.bundle.min.js")


def download_bootstrap_icons():
    """
    bootstrap-icons.css references its own woff2/woff font files via
    relative url(./fonts/bootstrap-icons.woff2?<hash>) style paths. We fetch
    the CSS, find every such reference, download each font file next to the
    CSS (preserving the ./fonts/ relative structure the CSS already
    expects), and save the CSS byte-for-byte otherwise - so no path
    rewriting is needed inside the CSS itself, only the outer <link> tag in
    our templates needed to change (already done).
    """
    css_bytes = _download(BOOTSTRAP_ICONS_CSS_URL, BOOTSTRAP_ICONS_DIR / "bootstrap-icons.css")
    css_text = css_bytes.decode("utf-8", errors="replace")

    font_refs = set(re.findall(r'url\((["\']?)(\./fonts/[^"\')]+)\1\)', css_text))
    if not font_refs:
        print("  WARNING: no ./fonts/... references found in bootstrap-icons.css - "
              "the icon font glyphs may not render. Check BOOTSTRAP_ICONS_VERSION.")
    for _, rel_ref in font_refs:
        # rel_ref looks like "./fonts/bootstrap-icons.woff2?1e0da5e4b7c8c8e2e7e5c8c8e2e7e5c8"
        rel_path = rel_ref.split("?", 1)[0]  # drop the cache-busting query string
        font_url = urljoin(BOOTSTRAP_ICONS_BASE, rel_path)
        dest = BOOTSTRAP_ICONS_DIR / rel_path.lstrip("./")
        _download(font_url, dest)


def download_chartjs():
    _download(CHARTJS_URL, CHARTJS_DIR / "chart.umd.min.js")


def download_google_fonts():
    """
    Fetches the Google Fonts CSS (with a browser UA so we get woff2 URLs),
    downloads every referenced font file into static/vendor/fonts/, and
    writes a LOCAL google-fonts.css with every url(https://fonts.gstatic....)
    rewritten to a local relative path. static/css/style.css already
    `@import`s this local file (see that file's OFFLINE ASSET NOTE comment)
    instead of the original fonts.googleapis.com URL.
    """
    resp = requests.get(GOOGLE_FONTS_CSS_URL, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=30)
    resp.raise_for_status()
    css_text = resp.text
    print(f"Downloaded Google Fonts CSS ({len(css_text):,} bytes)")

    font_urls = sorted(set(re.findall(r'url\((https://fonts\.gstatic\.com/[^)]+)\)', css_text)))
    if not font_urls:
        print("  WARNING: no fonts.gstatic.com URLs found - Google may have changed "
              "its response format. Font fallback to system fonts will occur.")

    rewritten_css = css_text
    for font_url in font_urls:
        parsed = urlparse(font_url)
        filename = os.path.basename(parsed.path)
        dest = FONTS_DIR / filename
        _download(font_url, dest)
        # Local reference is relative to static/vendor/fonts/google-fonts.css itself.
        rewritten_css = rewritten_css.replace(font_url, filename)

    out_path = FONTS_DIR / "google-fonts.css"
    header = (
        "/* Downloaded from Google Fonts by scripts/download_vendor_assets.py.\n"
        "   Font files are stored alongside this file in static/vendor/fonts/.\n"
        "   Do not hand-edit - re-run the script instead if fonts need to change. */\n"
    )
    out_path.write_text(header + rewritten_css, encoding="utf-8")
    print(f"  -> wrote {out_path.relative_to(REPO_ROOT)}")


def main():
    print(f"Vendor assets will be written under: {VENDOR_DIR}\n")
    download_bootstrap()
    print()
    download_bootstrap_icons()
    print()
    download_chartjs()
    print()
    download_google_fonts()
    print("\nAll vendor assets downloaded successfully.")
    print("The application will now render fully offline - no CDN calls remain.")


if __name__ == "__main__":
    main()
