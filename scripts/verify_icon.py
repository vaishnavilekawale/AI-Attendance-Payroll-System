"""
verify_icon.py
==============
Verifies that the application icon is correctly configured for both
PyInstaller and Inno Setup.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ICON_PATH = REPO_ROOT / "installer" / "app_icon.ico"

print("="*60)
print("ICON VERIFICATION")
print("="*60)

# Check if icon exists
if ICON_PATH.exists():
    print(f"[OK] Icon file exists: {ICON_PATH}")
    size_kb = ICON_PATH.stat().st_size / 1024
    print(f"     Size: {size_kb:.2f} KB")
else:
    print(f"[ERROR] Icon file NOT found at: {ICON_PATH}")
    print("\nTo fix this:")
    print("1. Create or download a professional .ico file")
    print("2. Save it as: installer/app_icon.ico")
    print("3. Recommended: Use a tool like GIMP, Photoshop, or online converters")
    print("4. The icon should be at least 256x256 pixels with multiple sizes")

# Check PyInstaller spec reference
spec_path = REPO_ROOT / "attendance_app.spec"
if spec_path.exists():
    with open(spec_path, 'r') as f:
        spec_content = f.read()
    if "installer/app_icon.ico" in spec_content:
        print(f"[OK] PyInstaller spec references the icon correctly")
    else:
        print(f"[WARNING] PyInstaller spec may not reference the icon")

# Check Inno Setup reference
iss_path = REPO_ROOT / "installer" / "AttendancePayrollSystem.iss"
if iss_path.exists():
    with open(iss_path, 'r') as f:
        iss_content = f.read()
    if "SetupIconFile=app_icon.ico" in iss_content:
        print(f"[OK] Inno Setup script references the icon correctly")
    else:
        print(f"[WARNING] Inno Setup script may not reference the icon")

print("="*60)
print("\nNEXT STEPS:")
print("1. If icon is missing, create one and place it at installer/app_icon.ico")
print("2. Test the build: pyinstaller attendance_app.spec --clean")
print("3. Verify the exe shows your icon in Windows Explorer")
print("4. Build the installer: ISCC.exe installer/AttendancePayrollSystem.iss")
print("5. Verify the installer shows your icon")
print("="*60)
