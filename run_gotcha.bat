@echo off
title goTCHA Server
echo ===================================================
echo Starting the goTCHA Edge API...
echo ===================================================
echo.

:: Bot-labeled data collection needs GOTCHA_ADMIN_KEY. There is deliberately no default: set your own
:: in the environment before running this script, and pass the same value to the bot scripts
:: (--admin-key or the GOTCHA_ADMIN_KEY env var).
if not defined GOTCHA_ADMIN_KEY echo GOTCHA_ADMIN_KEY is not set: bot-labeled data collection is disabled.

:: Start the Uvicorn server silently
python -m uvicorn backend_api:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log

pause
