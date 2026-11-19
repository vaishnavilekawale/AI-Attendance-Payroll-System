"""
Command-line restore for AI Attendance & Payroll System backups.

Use this when the app cannot start (new PC, crashed install) or when you want
to restore a backup file that is not in the backups folder.

    1. CLOSE the application first.
    2. Run from the install folder:

         python restore_backup.py --list
         python restore_backup.py "D:\\AttendancePayrollSystem_Backups\\automated_backup_20260101_020000.zip.enc"
         python restore_backup.py backup.zip --password "my backup password"
         python restore_backup.py backup.zip --no-env      (keep this PC's .env)

    3. Start the application again (run Train AI if face recognition looks off).

Your current data is saved first as backups\\pre_restore_<timestamp>.zip(.enc).
For a packaged .exe install, run this script from the install folder with
Python, or ask support - the restore also works from Settings -> Data Backup
-> Restore while the app is running.
"""
import argparse
import getpass
import os
import sys

import backup_manager as bm


def _list():
    items = bm.list_backups()
    if not items:
        print("No backup files found in:", ", ".join(bm.backup_search_dirs()) or bm.local_backup_dir())
        return 1
    for item in items:
        lock = " [encrypted]" if item["encrypted"] else ""
        safety = " [safety copy]" if item["is_safety_copy"] else ""
        print(f'{item["modified"]:%Y-%m-%d %H:%M}  {item["size"] / 1048576:8.1f} MB  '
              f'{os.path.join(item["folder"], item["name"])}{lock}{safety}')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Restore an AIAPS backup (close the app first).")
    parser.add_argument("backup", nargs="?", help="path to the .zip or .zip.enc backup file")
    parser.add_argument("--password", help="backup password (asked for if needed and omitted)")
    parser.add_argument("--no-env", action="store_true", help="do not restore the .env file")
    parser.add_argument("--list", action="store_true", help="list backups found in the backup folders")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)

    if args.list:
        return _list()
    if not args.backup:
        parser.print_help()
        return 2

    password = args.password
    if bm.is_encrypted_file(args.backup) and not password:
        password = getpass.getpass("Backup password: ")

    if not args.yes:
        print("This REPLACES the current database, uploads and face images with the backup.")
        print("Make sure the application is CLOSED. Current data is saved first as a pre_restore_* backup.")
        if input("Type RESTORE to continue: ").strip() != "RESTORE":
            print("Cancelled.")
            return 1

    result = bm.restore_backup(args.backup, password=password,
                               restore_env=not args.no_env, replace_db_file=True)
    if not result["success"]:
        print("RESTORE FAILED:", result["error"])
        print("Nothing was changed." if not result["safety_backup"]
              else f'A safety backup of your previous data is at: {result["safety_backup"]}')
        return 1

    print("Restore complete.")
    print("  Safety backup of previous data:", result["safety_backup"])
    print("  Files restored (uploads/dataset):", result["files_restored"])
    print("  .env restored:", result["env_restored"], "| license.lic restored:", result["license_restored"])
    print("Now start the application again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
