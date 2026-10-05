"""
prepare_deepface_weights.py
============================
Prepares DeepFace model weights for bundling into the PyInstaller build.
Run this before building the .exe to ensure face recognition works offline.
"""
import os
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Source: User's DeepFace cache directory
SOURCE_DIR = Path.home() / ".deepface" / "weights"
# Destination: Where PyInstaller will bundle them from
DEST_DIR = REPO_ROOT / "deepface_weights"

print("="*60)
print("DEEPFACE WEIGHTS PREPARATION")
print("="*60)

# Check if source weights exist
if not SOURCE_DIR.exists():
    print(f"[ERROR] DeepFace weights not found at: {SOURCE_DIR}")
    print("\nTo download weights:")
    print("1. Run the app: python app.py")
    print("2. Complete the setup wizard")
    print("3. Add a test employee")
    print("4. Capture at least one face photo")
    print("5. Exit the app")
    print("6. Run this script again")
    exit(1)

print(f"[OK] Source weights found: {SOURCE_DIR}")

# List available weights
weight_files = list(SOURCE_DIR.glob("*.h5"))
print(f"\nFound {len(weight_files)} weight file(s):")
for wf in weight_files:
    size_mb = wf.stat().st_size / (1024 * 1024)
    print(f"  - {wf.name} ({size_mb:.2f} MB)")

# Create destination directory
DEST_DIR.mkdir(exist_ok=True)
print(f"\n[OK] Destination directory: {DEST_DIR}")

# Copy weights
print("\nCopying weights...")
total_size = 0
for wf in weight_files:
    dest_file = DEST_DIR / wf.name
    shutil.copy2(wf, dest_file)
    size_mb = dest_file.stat().st_size / (1024 * 1024)
    total_size += size_mb
    print(f"  [OK] Copied {wf.name} ({size_mb:.2f} MB)")

print(f"\nTotal size: {total_size:.2f} MB")

# Set environment variable for PyInstaller
env_file = REPO_ROOT / ".env"
print(f"\n[INFO] To use these weights during PyInstaller build, set:")
print(f"       set DEEPFACE_WEIGHTS_DIR={DEST_DIR}")
print(f"\nOr add this line to your .env file:")
print(f"       DEEPFACE_WEIGHTS_DIR={DEST_DIR}")

# Update .env if it exists
if env_file.exists():
    with open(env_file, 'r') as f:
        env_content = f.read()

    if "DEEPFACE_WEIGHTS_DIR" not in env_content:
        with open(env_file, 'a') as f:
            f.write(f"\nDEEPFACE_WEIGHTS_DIR={DEST_DIR}\n")
        print(f"\n[OK] Added DEEPFACE_WEIGHTS_DIR to .env file")
    else:
        print(f"\n[INFO] DEEPFACE_WEIGHTS_DIR already exists in .env")
        print(f"      Please verify it points to: {DEST_DIR}")

print("="*60)
print("\nNEXT STEPS:")
print("1. Build with PyInstaller:")
print("   set DEEPFACE_WEIGHTS_DIR={DEST_DIR}")
print("   pyinstaller attendance_app.spec --clean")
print("\n2. Verify the build output includes deepface_weights folder")
print("3. Test the built exe by triggering face recognition")
print("="*60)
