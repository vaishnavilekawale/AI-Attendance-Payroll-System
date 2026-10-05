"""
update_contact_details.py
=========================
Updates placeholder contact information in EULA.txt and USER_MANUAL.md
with your actual business details.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EULA_PATH = REPO_ROOT / "licensing" / "EULA.txt"
USER_MANUAL_PATH = REPO_ROOT / "licensing" / "USER_MANUAL.md"

print("="*60)
print("CONTACT DETAILS UPDATE")
print("="*60)

# Get user input
print("\nPlease enter your business details:")
print("(Press Enter to keep current value if already set)")
print()

company_name = input("Company Name: ").strip()
support_email = input("Support Email: ").strip()
phone_number = input("Phone Number (e.g., +91 98765 43210): ").strip()
website = input("Website: ").strip()
city = input("City (for EULA jurisdiction): ").strip()

# Validate required fields
if not company_name:
    print("\n[ERROR] Company Name is required!")
    exit(1)
if not support_email:
    print("\n[ERROR] Support Email is required!")
    exit(1)

# Default values
if not phone_number:
    phone_number = "+91 XXXXX XXXXX"
if not website:
    website = "www.yourcompany.com"
if not city:
    city = "Your City"

print(f"\n{'='*60}")
print("Summary:")
print(f"  Company: {company_name}")
print(f"  Email: {support_email}")
print(f"  Phone: {phone_number}")
print(f"  Website: {website}")
print(f"  City: {city}")
print(f"{'='*60}")

confirm = input("\nProceed with these updates? (y/n): ").strip().lower()
if confirm != 'y':
    print("Aborted.")
    exit(0)

# Update EULA.txt
print(f"\nUpdating {EULA_PATH.name}...")
with open(EULA_PATH, 'r', encoding='utf-8') as f:
    eula_content = f.read()

eula_content = eula_content.replace("[Your Company Name]", company_name)
eula_content = eula_content.replace("[support@yourcompany.com]", support_email)
eula_content = eula_content.replace("[+91 XXXXX XXXXX]", phone_number)
eula_content = eula_content.replace("[www.yourcompany.com]", website)
eula_content = eula_content.replace("[Your City]", city)

with open(EULA_PATH, 'w', encoding='utf-8') as f:
    f.write(eula_content)

print(f"  [OK] Updated EULA.txt")

# Update USER_MANUAL.md
print(f"Updating {USER_MANUAL_PATH.name}...")
with open(USER_MANUAL_PATH, 'r', encoding='utf-8') as f:
    manual_content = f.read()

manual_content = manual_content.replace("[Your Company Name]", company_name)
manual_content = manual_content.replace("[support@yourcompany.com]", support_email)
manual_content = manual_content.replace("[+91 XXXXX XXXXX]", phone_number)
manual_content = manual_content.replace("[www.yourcompany.com]", website)

with open(USER_MANUAL_PATH, 'w', encoding='utf-8') as f:
    f.write(manual_content)

print(f"  [OK] Updated USER_MANUAL.md")

# Also update installer if needed
installer_path = REPO_ROOT / "installer" / "AttendancePayrollSystem.iss"
if installer_path.exists():
    print(f"\nUpdating {installer_path.name}...")
    with open(installer_path, 'r', encoding='utf-8') as f:
        installer_content = f.read()

    installer_content = installer_content.replace("Vaishnavi Maruti Lekawale", company_name)
    installer_content = installer_content.replace("lekawalevaishnavi@gmail.com", support_email)

    with open(installer_path, 'w', encoding='utf-8') as f:
        f.write(installer_content)

    print(f"  [OK] Updated AttendancePayrollSystem.iss")

print(f"\n{'='*60}")
print("SUCCESS!")
print("="*60)
print("\nUpdated files:")
print(f"  - {EULA_PATH}")
print(f"  - {USER_MANUAL_PATH}")
print(f"  - {installer_path}")
print("\nPlease review the updated files to ensure accuracy.")
print("="*60)
