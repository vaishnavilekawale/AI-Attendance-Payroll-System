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

:: 5. Install Dependencies
echo Installing dependencies from requirements.txt...
pip install -r requirements.txt

:: 6. Setup .env file
if not exist .env (
    if exist .env.example (
        copy .env.example .env
        echo .env file created from .env.example. Please update your configurations!
    )
)

echo ==========================================
echo Setup Completed Successfully!
echo Run 'python launcher.py' to start the app.
echo ==========================================
pause