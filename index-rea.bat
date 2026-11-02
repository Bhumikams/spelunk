@echo off
rem Index morluto/rea (or another repo: index-rea.bat https://github.com/owner/repo) into Atlas.
cd /d "%~dp0backend"
if not exist .venv\Scripts\python.exe (
  echo  Run start.bat once first to install everything.
  pause & exit /b 1
)
.venv\Scripts\python -m scripts.index_repo %*
echo.
pause
