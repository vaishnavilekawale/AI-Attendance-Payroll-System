@echo off
echo ==========================================
echo Setting up AI Attendance & Payroll System
echo ==========================================

:: 1. Check Python version
python --version

:: 2. Create Virtual Environment
echo Creating virtual environment (venv)...
python -m venv venv

:: 3. Activate Virtual Environment
call venv\Scripts\activate

:: 4. Upgrade Pip
echo Upgrading pip...
python -m pip install --upgrade pip

:: 5. Install Runtime Dependencies
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

:: 6. Install Packaging Dependencies (for PyInstaller)
if exist requirements-packaging.txt (
    echo Installing packaging dependencies...
    pip install -r requirements-packaging.txt
)

:: 7. Setup .env file
if not exist .env (
    if exist .env.example (
        copy .env.example .env
        echo .env file created from .env.example. Please update your configurations!
    )
)

:: 8. Ensure necessary folders exist (Git ignores empty folders)
if not exist dataset mkdir dataset
if not exist instance mkdir instance
if not exist uploads mkdir uploads
if not exist trained_model mkdir trained_model

:: 9. Build .exe using PyInstaller Spec file automatically
echo ==========================================
echo Building .exe file using PyInstaller...
echo ==========================================
pyinstaller attendance_app.spec --clean

echo ==========================================
echo Setup and .exe Build Completed Successfully!
echo Your executable is ready inside the 'dist' folder.
echo ==========================================
pause