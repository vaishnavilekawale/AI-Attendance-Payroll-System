@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==========================================================
echo   AI Attendance ^& Payroll System - Setup
echo ==========================================================
echo.

:: ---------------------------------------------------------
:: 1. Find Python 3.10 64-bit. If missing, install 3.10.11 automatically.
::    (the pinned numpy 1.26 / OpenCV 4.8 wheels are built for 3.10)
:: ---------------------------------------------------------
call :findpython
if defined PY goto :pyfound

echo [..] Python 3.10 ^(64-bit^) was not found on this PC.
call :installpython
if errorlevel 1 goto :nopython
call :findpython
if not defined PY goto :nopython

:pyfound
echo [OK] Using Python:
%PY% --version
echo.

:: ---------------------------------------------------------
:: 2. Virtual environment
:: ---------------------------------------------------------
if not exist venv\Scripts\activate.bat (
    echo [..] Creating virtual environment ^(venv^)...
    %PY% -m venv venv
    if errorlevel 1 goto :fail
) else (
    echo [OK] venv already exists.
)
call venv\Scripts\activate.bat
if errorlevel 1 goto :fail

:: ---------------------------------------------------------
:: 3. Install dependencies (stops on first error)
:: ---------------------------------------------------------
echo [..] Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :fail

:: Only ONE OpenCV package may exist (they all provide the same `cv2` module).
:: requirements.txt uses opencv-python-headless, so remove the other variants.
pip uninstall -y opencv-python opencv-contrib-python opencv-contrib-python-headless >nul 2>&1

echo [..] Installing requirements.txt ^(about 200 MB, please wait^)...
pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [..] Checking that OpenCV and the face engine import correctly...
python -c "import cv2, lite_face; print('Face engine OK', cv2.__version__, cv2.imread)"
if errorlevel 1 goto :fail

:: ---------------------------------------------------------
:: 4. .env and runtime folders
:: ---------------------------------------------------------
if not exist .env (
    if exist .env.example (
        copy .env.example .env >nul
        echo [OK] .env created from .env.example - edit it for email settings.
    )
)
:: Vendor portal settings (only needed on YOUR vendor server, never given to customers)
if not exist licensing\.env.vendor (
    if exist licensing\.env.vendor.example (
        copy licensing\.env.vendor.example licensing\.env.vendor >nul
        echo [OK] licensing\.env.vendor created - fill Razorpay, email and admin password
        echo      ONLY on your vendor server. Customers never need this file.
    )
)
if not exist dataset mkdir dataset
if not exist instance mkdir instance
if not exist uploads mkdir uploads
if not exist trained_model mkdir trained_model
if not exist logs mkdir logs

:: ---------------------------------------------------------
:: 4b. Face-recognition models (OpenCV YuNet + SFace, about 38 MB, one time)
:: ---------------------------------------------------------
echo [..] Downloading face models into face_models ...
python scripts\download_face_models.py
if errorlevel 1 (
    echo [WARN] Face model download failed. Check your internet and run:
    echo        python scripts\download_face_models.py
)

:: ---------------------------------------------------------
:: 5. Offline UI assets (Bootstrap, icons, Chart.js, fonts)
:: ---------------------------------------------------------
echo [..] Downloading UI assets into static\vendor ...
python scripts\download_vendor_assets.py
if errorlevel 1 (
    echo [WARN] UI assets download failed. The pages will look unstyled.
    echo        Check your internet and run:  python scripts\download_vendor_assets.py
)

echo.
echo ==========================================================
echo   Setup finished. Nothing has been built yet.
echo ==========================================================

:menu
echo.
echo   1 - Run the app now ^(development mode^)
echo   2 - Build the .exe + installer ^(PyInstaller, then Inno Setup - slow^)
echo   3 - Exit
choice /c 123 /n /m "Choose 1-3: "
if errorlevel 3 goto :done
if errorlevel 2 goto :build
if errorlevel 1 goto :runapp
goto :menu

:runapp
echo.
echo Starting at http://127.0.0.1:5000  ^(Ctrl+C to stop^)
echo First run opens the Setup Wizard. The face models were downloaded
echo during setup ^(face_models folder^).
set FLASK_ENV=development
python app.py
goto :menu

:build
echo.
echo [..] Installing packaging tools...
pip install -r requirements-packaging.txt
if errorlevel 1 goto :fail
echo [..] Making sure the face models are present ^(bundled for offline PCs^)...
python scripts\download_face_models.py
if errorlevel 1 (
    echo [WARN] Could not download the face models. Check your internet,
    echo        run python scripts\download_face_models.py, then build again.
)
echo [..] Building .exe ...
pyinstaller attendance_app.spec --clean
if errorlevel 1 (
    echo.
    echo [WARN] PyInstaller failed. Retrying once ^(a security program can briefly
    echo        block a child process; if it fails again, see README Troubleshooting^)...
    pyinstaller attendance_app.spec --clean
    if errorlevel 1 goto :fail
)
echo.
echo Build done. Output is in the 'dist\AttendancePayrollSystem' folder.
echo Look above for the line "Bundling face models from:". If you see
echo "WARNING: face_models folder not found", the exe will not work offline.
goto :makeinstaller

:: ---------------------------------------------------------
:: Inno Setup: wrap dist\AttendancePayrollSystem into ONE Setup.exe
:: Installs Inno Setup 6 automatically if it is missing.
:: ---------------------------------------------------------
:makeinstaller
echo.
if not exist "dist\AttendancePayrollSystem\AttendancePayrollSystem.exe" (
    echo [ERROR] dist\AttendancePayrollSystem was not found.
    echo         Choose option 2 first ^(PyInstaller build^), then try again.
    goto :menu
)
call :findinno
if defined ISCC goto :compile

echo [..] Inno Setup 6 was not found on this PC.
call :installinno
call :findinno
if not defined ISCC goto :noinno

:compile
echo [OK] Using Inno Setup: %ISCC%
echo [..] Compiling installer with Inno Setup...
"%ISCC%" installer\AttendancePayrollSystem.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Inno Setup failed. Scroll up to read the message.
    echo         ^(A missing wizard_*.bmp / app_icon.ico is the usual cause.^)
    goto :menu
)
echo.
echo ==========================================================
echo   Installer ready - give THIS file to the customer:
echo   installer\Output\AttendancePayrollSystem-Setup-1.0.0.exe
echo   ^(Do NOT give the exe from the dist folder - it needs its folder.^)
echo ==========================================================
if exist installer\Output start "" explorer installer\Output
goto :menu

:noinno
echo [WARN] Automatic install of Inno Setup 6 did not work, so the Setup.exe
echo        could not be created. The dist folder is fine and was kept.
echo.
echo        1. Download and install Inno Setup 6 ^(free, keep default options^):
echo           https://jrsoftware.org/isdl.php
echo        2. Run this file again and choose option 2.
echo.
choice /c YN /n /m "Open the Inno Setup download page now? (Y/N): "
if errorlevel 2 goto :menu
start "" https://jrsoftware.org/isdl.php
goto :menu

:nopython
echo.
echo [ERROR] Python 3.10 ^(64-bit^) could not be found or installed automatically.
echo         Install Python 3.10.11 manually from
echo         https://www.python.org/downloads/release/python-31011/
echo         and tick "Add Python to PATH". Python 3.11/3.12/3.13 will NOT work.
goto :end

:fail
echo.
echo ==========================================================
echo   [ERROR] A step failed. Scroll up to read the error message.
echo ==========================================================
goto :end

:done
echo Bye.

:end
echo.
pause
endlocal
exit /b

:: =========================================================
:: Helper subroutines (only reached through CALL)
:: =========================================================

:: ---- Sets PY to a working Python 3.10 64-bit command, or leaves it empty
:findpython
set "PY="
py -3.10-64 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3.10-64"
    exit /b 0
)
call :trypy python
if defined PY exit /b 0
call :trypy "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if defined PY exit /b 0
call :trypy "%ProgramFiles%\Python310\python.exe"
exit /b 0

:trypy
if /i not "%~1"=="python" if not exist "%~1" exit /b 1
"%~1" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,10) and sys.maxsize>2**32 else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set PY="%~1"
exit /b 0

:: ---- Downloads and silently installs Python 3.10.11 for the current user
:installpython
set "PYEXE=%TEMP%\python-3.10.11-amd64.exe"
echo [..] Downloading Python 3.10.11 ^(about 28 MB^)...
call :download "https://www.python.org/ftp/python/3.10.11/python-3.10.11-amd64.exe" "%PYEXE%"
if errorlevel 1 (
    echo [WARN] Could not download Python. Check your internet connection.
    exit /b 1
)
echo [..] Installing Python 3.10.11 for this user ^(silent, 1-2 minutes^)...
start /wait "" "%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0 Shortcuts=0
set "RC=%errorlevel%"
del "%PYEXE%" >nul 2>&1
if not "%RC%"=="0" (
    echo [WARN] Python installer returned code %RC%.
    exit /b 1
)
echo [OK] Python 3.10.11 installed.
exit /b 0

:: ---- Sets ISCC to the Inno Setup 6 compiler path, or leaves it empty
:findinno
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
exit /b 0

:: ---- Downloads and silently installs Inno Setup 6 for the current user.
::      Falls back to winget if the direct download/installer does not work.
:installinno
set "ISEXE=%TEMP%\innosetup-installer.exe"
echo [..] Downloading Inno Setup 6 ^(about 7 MB^)...
call :download "https://jrsoftware.org/download.php/is.exe" "%ISEXE%"
if errorlevel 1 goto :inno_winget
:: make sure we got a real Windows program (starts with "MZ"), not an HTML page
powershell -NoProfile -Command "$b=[IO.File]::ReadAllBytes('%ISEXE%'); if($b.Length -gt 1000000 -and $b[0] -eq 77 -and $b[1] -eq 90){exit 0}else{exit 1}"
if errorlevel 1 (
    echo [WARN] The downloaded file is not a valid installer.
    goto :inno_winget
)
echo [..] Installing Inno Setup 6 for this user ^(silent^)...
start /wait "" "%ISEXE%" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER
call :findinno
if defined ISCC (
    del "%ISEXE%" >nul 2>&1
    exit /b 0
)
:inno_winget
del "%ISEXE%" >nul 2>&1
where winget >nul 2>&1
if errorlevel 1 exit /b 1
echo [..] Trying to install Inno Setup 6 with winget ^(allow the admin prompt if asked^)...
winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
exit /b 0

:: ---- download <url> <outfile>   (uses built-in PowerShell, TLS 1.2)
:download
if exist "%~2" del "%~2" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'"
if errorlevel 1 exit /b 1
if not exist "%~2" exit /b 1
exit /b 0