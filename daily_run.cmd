@echo off
rem The daily run, started by Windows Task Scheduler at 12:45, a little after the Tech Map's daily update.
rem Its output goes to logs\daily_run.log; the jobs page shows the results as after any run.
cd /d "%~dp0"
if not exist logs mkdir logs
echo ==== %date% %time% >> "logs\daily_run.log"
".venv\Scripts\python.exe" run.py >> "logs\daily_run.log" 2>&1
