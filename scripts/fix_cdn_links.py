"""
fix_cdn_links.py
================
Updates all HTML templates to use local vendor assets instead of CDN links.
Run this after running download_vendor_assets.py to make the app fully offline-capable.
"""
import os
import re
from pathlib import Path

# Paths
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"

# CDN to local mappings
REPLACEMENTS = [
    # Bootstrap CSS
    (
        r'<link href="https://cdn\.jsdelivr\.net/npm/bootstrap@5\.3\.0/dist/css/bootstrap\.min\.css"[^>]*>',
        '<link href="{{ url_for(\'static\', filename=\'vendor/bootstrap/css/bootstrap.min.css\') }}" rel="stylesheet">'
    ),
    # Bootstrap JS
    (
        r'<script src="https://cdn\.jsdelivr\.net/npm/bootstrap@5\.3\.0/dist/js/bootstrap\.bundle\.min\.js"[^>]*></script>',
        '<script src="{{ url_for(\'static\', filename=\'vendor/bootstrap/js/bootstrap.bundle.min.js\') }}"></script>'
    ),
    # Bootstrap Icons CSS
    (
        r'<link rel="stylesheet" href="https://cdn\.jsdelivr\.net/npm/bootstrap-icons@1\.10\.0/font/bootstrap-icons\.css"[^>]*>',
        '<link rel="stylesheet" href="{{ url_for(\'static\', filename=\'vendor/bootstrap-icons/bootstrap-icons.css\') }}">'
    ),
    # Chart.js (various versions)
    (
        r'<script src="https://cdn\.jsdelivr\.net/npm/chart\.js[^"]*"[^>]*></script>',
        '<script src="{{ url_for(\'static\', filename=\'vendor/chartjs/chart.umd.min.js\') }}"></script>'
    ),
]

def fix_template_file(file_path):
    """Update a single HTML template file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    changes_made = 0

    for pattern, replacement in REPLACEMENTS:
        matches = re.findall(pattern, content)
        if matches:
            content = re.sub(pattern, replacement, content)
            changes_made += len(matches)

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return changes_made
    return 0

def main():
    """Update all HTML templates."""
    print(f"Scanning templates directory: {TEMPLATES_DIR}\n")

    html_files = list(TEMPLATES_DIR.glob("*.html"))
    if not html_files:
        print("No HTML files found in templates directory!")
        return

    total_changes = 0
    files_updated = 0

    for html_file in html_files:
        changes = fix_template_file(html_file)
        if changes > 0:
            files_updated += 1
            total_changes += changes
            print(f"[OK] Updated {html_file.name} ({changes} change(s))")
        else:
            print(f"  - Skipped {html_file.name} (no CDN links found)")

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Files updated: {files_updated}/{len(html_files)}")
    print(f"  Total replacements: {total_changes}")
    print(f"{'='*60}")
    print("\nAll templates now use local vendor assets.")
    print("The application is fully offline-capable!")

if __name__ == "__main__":
    main()
