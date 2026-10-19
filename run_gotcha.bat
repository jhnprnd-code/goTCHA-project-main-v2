@echo off
title goTCHA Server
echo ===================================================
echo Starting the goTCHA Edge API...
echo ===================================================
echo.

:: Admin key for bot labeling. An already-set GOTCHA_ADMIN_KEY wins; the fallback is a
:: local-testing placeholder, so set a long one yourself before exposing the server.
if not defined GOTCHA_ADMIN_KEY set GOTCHA_ADMIN_KEY=test-admin-key-123

:: Start the Uvicorn server silently
python -m uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log

pause
