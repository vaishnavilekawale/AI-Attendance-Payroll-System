@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ==========================================================
echo   AI Attendance ^& Payroll System - Setup
echo ==========================================================
echo.

:: ---------------------------------------------------------
:: 1. Find Python 3.10 (TensorFlow 2.15 needs 3.10 on Windows)
:: ---------------------------------------------------------
set "PY="
py -3.10 -c "import sys" >nul 2>&1 && set "PY=py -3.10"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,10) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY goto :nopython
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

:: plain opencv-python conflicts with opencv-contrib-python
pip uninstall -y opencv-python opencv-python-headless >nul 2>&1

echo [..] Installing requirements.txt ^(several GB, please wait^)...
pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [..] Checking that the heavy ML packages import correctly...
python -c "import cv2, tensorflow, mediapipe, deepface; print('ML stack OK')"
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
echo   2 - Build the .exe ^(PyInstaller, slow^)
echo   3 - Exit
choice /c 123 /n /m "Choose 1-3: "
if errorlevel 3 goto :done
if errorlevel 2 goto :build
if errorlevel 1 goto :runapp
goto :menu

:runapp
echo.
echo Starting at http://127.0.0.1:5000  ^(Ctrl+C to stop^)
echo First run opens the Setup Wizard. The first face registration
echo downloads the face-model weights, so keep internet ON.
set FLASK_ENV=development
python app.py
goto :menu

:build
echo.
echo [..] Installing packaging tools...
pip install -r requirements-packaging.txt
if errorlevel 1 goto :fail
echo [..] Pre-downloading face-model weights ^(optional, needed for offline PCs^)...
python -c "from deepface import DeepFace; DeepFace.build_model('Facenet512'); print('weights OK')"
if errorlevel 1 (
    echo [WARN] Could not pre-download weights. Register one test face with
    echo        python app.py first, then build again.
)
echo [..] Building .exe ...
pyinstaller attendance_app.spec --clean
if errorlevel 1 goto :fail
echo.
echo Build done. Output is in the 'dist\AttendancePayrollSystem' folder.
echo Look above for the line "Bundling DeepFace weights from:". If you see
echo "WARNING: DeepFace weights folder not found", the exe will not work offline.
goto :menu

:nopython
echo.
echo [ERROR] Python 3.10 was not found.
echo         Install Python 3.10.11 from python.org and tick
echo         "Add Python to PATH". Python 3.11/3.12/3.13 will NOT work.
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