; =============================================================================
; AttendancePayrollSystem.iss
; Inno Setup script for the AI Attendance & Payroll System.
;
; WHAT THIS BUILDS
; -----------------
; Wraps the PyInstaller onedir output (dist\AttendancePayrollSystem\) into a
; single distributable installer .exe that:
;   - Installs into a WRITABLE per-user directory under %LOCALAPPDATA%
;     (never Program Files - see the long comment under [Setup] below for why
;     this matters for THIS specific app).
;   - Creates a Start Menu program group with a shortcut to the app and to
;     the uninstaller.
;   - Optionally creates a Desktop shortcut (user opt-in checkbox, unticked
;     by default - standard Inno Setup convention).
;   - Registers a proper "Add or Remove Programs" uninstall entry that
;     removes the *program files* it installed, while intentionally leaving
;     the customer's DATA (database, uploads, dataset, logs, .env) in place -
;     see [UninstallDelete] below for the reasoning and how to opt into full
;     data removal for a "clean uninstall" build variant.
;
; BEFORE COMPILING THIS SCRIPT
; -----------------------------
; 1. Run scripts\download_vendor_assets.py (see README.md Section 5.3) so
;    the UI renders fully offline.
; 2. Populate DeepFace model weights and build with PyInstaller (README.md
;    Sections 5 and 6):
;        pyinstaller attendance_app.spec --clean
;    This must produce a populated dist\AttendancePayrollSystem\ folder
;    (containing AttendancePayrollSystem.exe and all its bundled data)
;    BEFORE you compile this .iss file - Inno Setup only packages files
;    that already exist on disk at compile time.
; 3. Put a real application icon at installer\app_icon.ico (referenced
;    below). A placeholder is NOT included in this repo - if you don't have
;    one yet, comment out the two "IconFilename"/SetupIconFile lines marked
;    below and Inno Setup will fall back to its default icon.
; 4. Compile with the Inno Setup Compiler (GUI: open this file and press
;    Build > Compile) or from the command line:
;        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\AttendancePayrollSystem.iss
;    Output installer .exe is written to installer\Output\ (see OutputDir).
; =============================================================================

#define MyAppName "AI Attendance & Payroll System"
#define MyAppExeName "AttendancePayrollSystem.exe"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Your Company Name"
#define MyAppURL "https://example.com"
; Path (relative to this .iss file) to the PyInstaller onedir output folder.
#define MySourceDir "..\dist\AttendancePayrollSystem"

[Setup]
; ---------------------------------------------------------------------------
; WHY THIS INSTALLS TO {localappdata}, NOT {autopf} (Program Files):
;
; This application persists its own data - SQLite database (instance\), face
; photos (dataset\), uploaded files (uploads\), trained model files
; (trained_model\), rotating logs (logs\), and an auto-generated .env with a
; per-install SECRET_KEY - all written NEXT TO the .exe itself at runtime
; (see config.py's BASE_DIR resolution, which uses sys.executable's own
; folder in a frozen build). Program Files is locked down by Windows for
; standard user accounts: writing there either fails outright or forces a
; UAC elevation prompt on literally every write, including the very first
; database creation on first launch. Installing per-user under
; %LOCALAPPDATA% (which is always fully writable by the owning user, no
; elevation required) means:
;   - No UAC prompt is needed to INSTALL the app (PrivilegesRequired=lowest
;     below), which matters for the non-technical office admins this app
;     ships to, who often don't have local admin rights on their PC.
;   - The app can read/write its own data folder at runtime with zero
;     elevation and zero permission errors on first launch.
;
; IF YOUR ORGANIZATION REQUIRES A MACHINE-WIDE (Program Files) INSTALL
; INSTEAD: change PrivilegesRequired to "admin", change DefaultDirName to
; "{autopf}\{#MyAppName}", and - critically - you must ALSO change
; config.py/attendance_app.spec so BASE_DIR resolves to a location standard
; (non-admin) users can still write to at runtime, such as
; "{commonappdata}\AttendancePayrollSystem" (%PROGRAMDATA%), since Program
; Files itself remains read-only to standard users even after an admin
; installs the app there. Do not simply flip these two lines without also
; making that BASE_DIR change, or every non-admin user's first launch will
; fail to create the database.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\AttendancePayrollSystem
; Allow a user who previously ran an admin-mode install to still upgrade
; without Inno Setup complaining about the privilege mismatch.
PrivilegesRequiredOverridesAllowed=dialog

AppId={{B4E1B6C0-9D4A-4B7E-8B1E-1F8D3A6C2E11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; No DisableProgramGroupPage - customer may want to rename the Start Menu
; folder, and this is not a machine-wide install where that matters much.
DefaultGroupName={#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=no
; Per-user install - no need for a "for all users / just me" radio choice.
;
; Produces AttendancePayrollSystem-Setup-<version>.exe in installer\Output\.
OutputDir=Output
OutputBaseFilename=AttendancePayrollSystem-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; A real .ico makes the installer and Start Menu/Desktop shortcuts look
; professional instead of using Inno Setup's generic default icon. Comment
; both of the following two lines out if you don't have an icon file yet -
; the installer will still build and work fine without them.
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; Shown on the "ready to install" page and in Add/Remove Programs.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
; 64-bit only - matches typical PyInstaller/TensorFlow/DeepFace builds on
; Windows, which do not ship 32-bit wheels for these dependencies.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Unticked by default (Flags: unchecked) - the customer explicitly opts in
; to a Desktop shortcut rather than it being forced on them.
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Recurse-copies the ENTIRE PyInstaller onedir output (the .exe plus every
; bundled DLL, the _internal folder, static/, templates/, dataset/,
; instance/, uploads/, trained_model/, etc.) into the install directory,
; preserving its internal folder structure exactly as PyInstaller built it.
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch the app immediately after a successful install, like most
; consumer Windows installers. Unticked would also be reasonable; ticked
; (default) matches the common convention for desktop apps like this one.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, "&", "&&")}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; ---------------------------------------------------------------------------
; DELIBERATELY CONSERVATIVE: Inno Setup's default uninstall behavior already
; removes every file that [Files] installed (the program binaries, static
; assets, templates, DLLs, etc.) - that part needs no extra configuration.
;
; What Inno Setup's default uninstall does NOT do is delete files created
; AFTER installation, at runtime, by the app itself - which for this app
; includes:
;     {app}\instance\attendance.db   (the SQLite database - all attendance,
;                                      payroll, and employee records)
;     {app}\uploads\                 (profile photos, generated payslip PDFs)
;     {app}\dataset\                 (captured face images)
;     {app}\trained_model\           (face recognition encodings)
;     {app}\logs\                    (rotating app.log files)
;     {app}\.env                     (auto-generated SECRET_KEY + any
;                                      customer-set SMTP/company settings)
;
; This is INTENTIONAL: an admin who uninstalls to troubleshoot, or to
; reinstall a newer version, should not silently lose the company's entire
; attendance/payroll history and every employee's captured face data. Data
; loss of this kind should require an explicit, separate, unmistakable
; action - not be a side effect of clicking "Uninstall".
;
; If you specifically need a "full clean uninstall" build variant (e.g. for
; a demo/trial installer that should leave no trace), uncomment the block
; below - but do this in a clearly-labeled separate build of the installer,
; not silently in the default production installer customers use for their
; real company data.
;
; Type: filesandordirs; Name: "{app}\instance"
; Type: filesandordirs; Name: "{app}\uploads"
; Type: filesandordirs; Name: "{app}\dataset"
; Type: filesandordirs; Name: "{app}\trained_model"
; Type: filesandordirs; Name: "{app}\logs"
; Type: files; Name: "{app}\.env"

[Code]
// Warn (but do not block) if the expected PyInstaller output wasn't found
// at compile time - this only fires if someone compiles this script
// without having run PyInstaller first, and it's a compile-time check, not
// a runtime one, so it's implemented as a pre-compile guard message instead
// of Pascal Script (Inno Setup has no reliable "does this file exist on the
// BUILD machine" pre-check hook outside of the [Files] source resolution
// itself, which will simply fail to compile with a clear "source file not
// found" error - that failure message IS the safeguard here).
