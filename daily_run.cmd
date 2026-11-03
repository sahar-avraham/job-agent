@echo off
rem The daily run, started by Windows Task Scheduler a little after the Tech Map's daily update.
rem Task Scheduler starts it through "conhost --headless", so no window opens that could be closed mid-run.
rem Its output goes to logs\daily_run.log, line by line; the jobs page shows the results as after any run.
cd /d "%~dp0"
if not exist logs mkdir logs
echo ==== %date% %time% >> "logs\daily_run.log"
".venv\Scripts\python.exe" -u run.py >> "logs\daily_run.log" 2>&1
