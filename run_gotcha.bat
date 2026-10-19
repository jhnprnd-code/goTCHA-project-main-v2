@echo off
title goTCHA Server
echo ===================================================
echo Starting the goTCHA Edge API...
echo ===================================================
echo.

:: Admin key for bot labeling. An already-set GOTCHA_ADMIN_KEY wins; otherwise use this default.
:: Pass the same value to the bot scripts (--admin-key or the GOTCHA_ADMIN_KEY env var).
if not defined GOTCHA_ADMIN_KEY set GOTCHA_ADMIN_KEY=120522

:: Start the Uvicorn server silently
python -m uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log

pause
