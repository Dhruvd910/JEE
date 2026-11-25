@echo off
rem Starts the public JEE Workbench site (serve_public.py) and restarts it if it ever stops.
rem Keep this window open (minimise it): closing it stops the site. Output is also kept in live\site.log.
title JEE Workbench live site
cd /d "%~dp0"
if not exist live mkdir live
:run
echo [%date% %time%] Starting JEE Workbench... (log: %~dp0live\site.log)
echo [%date% %time%] start >> live\site.log
python -u serve_public.py >> live\site.log 2>&1
echo [%date% %time%] The site stopped (exit %errorlevel%); restarting in 10 seconds. Close this window to stop it for good.
echo [%date% %time%] stopped (exit %errorlevel%) >> live\site.log
timeout /t 10 /nobreak >nul
goto run
