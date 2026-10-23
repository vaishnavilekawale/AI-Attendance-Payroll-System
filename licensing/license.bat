@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title AI Attendance and Payroll - Licensing (VENDOR tool)

:: =========================================================================
::  license.bat  -  ONE file for everything licensing (VENDOR side only).
::
::  It creates these files for you, only when they are missing
::  (licensing\.env.vendor is made by setup.bat, not here):
::     licensing\vendor_private_key.pem   signing key         (init-keys)
::     licensing\customers.db             customer database   (portal / init)
::     licensing\license_keys_log.txt     issue log           (first "issue")
::  and it writes the matching PUBLIC key into licensing\license_manager.py.
::
::  Run setup.bat once first (it creates the venv and installs packages).
::  NEVER give this file, the .pem, .env.vendor or customers.db to a customer.
:: =========================================================================

if not exist venv\Scripts\activate.bat (
    echo [ERROR] venv not found. Run setup.bat first ^(it creates venv and installs packages^).
    goto :end
)
call venv\Scripts\activate.bat
if errorlevel 1 goto :fail

echo ==========================================================
echo   Licensing - automatic first-time preparation
echo ==========================================================

:: ---- 1. signing key (.pem) + embed public key in license_manager.py -----
if not exist licensing\vendor_private_key.pem (
    echo [..] Creating signing key ^(vendor_private_key.pem^) and embedding the public key...
    python licensing\keygen.py init-keys
    if errorlevel 1 goto :fail
    set "KEY_IS_NEW=1"
) else (
    echo [OK] vendor_private_key.pem exists.
)
python licensing\keygen.py check-key
if errorlevel 1 (
    echo [ERROR] The private key and the key inside license_manager.py do not match.
    echo         Use menu 6 ^(new key^) or restore the right .pem file.
    goto :menu
)

:: ---- 2. customer database (.db) -----------------------------------------
if not exist licensing\customers.db (
    echo [..] Creating licensing\customers.db ...
    python -m licensing.customer_models
    if errorlevel 1 goto :fail
) else (
    echo [OK] customers.db exists.
)

if defined KEY_IS_NEW (
    echo.
    echo ----------------------------------------------------------
    echo  NEW KEY CREATED. Back up licensing\vendor_private_key.pem
    echo  somewhere offline. Any customer .exe built BEFORE this key
    echo  will NOT accept licenses - build the .exe again ^(setup.bat, option 2^).
    echo ----------------------------------------------------------
)

:menu
echo.
echo ==========================================================
echo   1 - Start the vendor portal      ^(buy page + webhook + customers.db^)
echo   2 - Issue a license manually     ^(needs customer's fingerprint^)
echo   3 - Show THIS PC's fingerprint   ^(for testing on your own PC^)
echo   4 - Run the customer app         ^(python app.py, port 5000^)
echo   5 - Check key match              ^(.pem  vs  license_manager.py^)
echo   6 - Create a NEW key             ^(invalidates old licenses!^)
echo   7 - Exit
echo ==========================================================
choice /c 1234567 /n /m "Choose 1-7: "
if errorlevel 7 goto :end
if errorlevel 6 goto :rotate
if errorlevel 5 goto :checkkey
if errorlevel 4 goto :runapp
if errorlevel 3 goto :fingerprint
if errorlevel 2 goto :issue
if errorlevel 1 goto :portal
goto :menu

:portal
echo.
findstr /b /i "VENDOR_DEV_MODE=true" licensing\.env.vendor >nul 2>&1
if not errorlevel 1 (
    echo Mode: DEV ^(fake payments, no Razorpay needed^)
) else (
    echo Mode: REAL payments - Razorpay keys must be filled in licensing\.env.vendor
)
echo Portal: http://127.0.0.1:8000     ^(Ctrl+C to stop, then press a key^)
echo.
python -m licensing.vendor_app
pause
goto :menu

:issue
echo.
set "FP="
set "CNAME="
set "PLAN="
set /p FP="Customer fingerprint (64 hex characters): "
if not defined FP goto :menu
set /p CNAME="Customer name: "
if not defined CNAME goto :menu
set /p PLAN="Plan - monthly / yearly / lifetime [yearly]: "
if not defined PLAN set "PLAN=yearly"
python licensing\keygen.py issue "!FP!" "!CNAME!" --plan !PLAN!
echo.
echo Send the long token to the customer. They paste it on the License screen
echo ^(or save it as license.lic next to the app^).
pause
goto :menu

:fingerprint
echo.
python -m licensing.license_manager fingerprint
pause
goto :menu

:runapp
echo.
echo Starting the customer app at http://127.0.0.1:5000 ^(Ctrl+C to stop^)
set FLASK_ENV=development
python app.py
pause
goto :menu

:checkkey
echo.
python licensing\keygen.py check-key
pause
goto :menu

:rotate
echo.
echo WARNING: a new key makes every license issued so far invalid, and every
echo          customer .exe must be rebuilt. The old .pem is kept as .revoked-...
choice /c YN /n /m "Really create a new key? (Y/N): "
if errorlevel 2 goto :menu
python licensing\keygen.py init-keys --rotate
pause
goto :menu

:fail
echo.
echo [ERROR] A step failed. Scroll up to read the message.

:end
echo.
pause
endlocal
exit /b