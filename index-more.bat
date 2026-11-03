@echo off
rem Index extra demo repos: Flask and Requests (about a minute each).
cd /d "%~dp0backend"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python -m scripts.index_repo https://github.com/pallets/flask > ..\index_more_log.txt 2>&1
.venv\Scripts\python -m scripts.index_repo https://github.com/psf/requests >> ..\index_more_log.txt 2>&1
echo DONE>> ..\index_more_log.txt
type ..\index_more_log.txt
pause
