@echo off
setlocal EnableDelayedExpansion
title Spelunk - ask a codebase
cd /d "%~dp0"

echo.
echo  Spelunk: hybrid search over a codebase
echo  ======================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo  Python is not installed. Install Python 3.10+ from python.org
  echo  and tick "Add python.exe to PATH" during setup, then run this again.
  pause & exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
  echo  Node.js is not installed. Install the LTS version from nodejs.org, then run this again.
  pause & exit /b 1
)
where git >nul 2>nul
if errorlevel 1 (
  echo  Git is not installed. Install it from git-scm.com, then run this again.
  pause & exit /b 1
)

rem ---- .env: Groq key and MongoDB URI, asked once ------------------------------
if not exist backend\.env copy backend\.env.example backend\.env >nul
findstr /c:"GROQ_API_KEY=your_groq_key_here" backend\.env >nul
if not errorlevel 1 (
  echo  You need a free Groq API key: https://console.groq.com/keys
  set /p GROQKEY= Paste your key ^(starts with gsk_^) and press Enter:
  powershell -NoProfile -Command "(Get-Content backend\.env) -replace '^GROQ_API_KEY=.*','GROQ_API_KEY=!GROQKEY!' | Set-Content backend\.env"
)
findstr /c:"MONGODB_URI=mongodb+srv://<user>" backend\.env >nul
if not errorlevel 1 (
  echo.
  echo  Paste your MongoDB Atlas connection string ^(Atlas ^> Connect ^> Drivers^),
  echo  or just press Enter to use the in-memory store ^(the index is lost when you close it^).
  set "MONGOURI="
  set /p MONGOURI= Connection string:
  if "!MONGOURI!"=="" (
    powershell -NoProfile -Command "(Get-Content backend\.env) -replace '^STORE=.*','STORE=memory' | Set-Content backend\.env"
  ) else (
    powershell -NoProfile -Command "$u = $env:MONGOURI; (Get-Content backend\.env) | ForEach-Object { if ($_ -like 'MONGODB_URI=*') { 'MONGODB_URI=' + $u } else { $_ } } | Set-Content backend\.env"
  )
)

rem ---- Backend setup (first run only) -------------------------------------------
if not exist backend\.venv\installed-v1.txt (
  echo.
  echo  First run: installing the backend. This takes a few minutes once.
  if not exist backend\.venv\Scripts\python.exe (
    python -m venv backend\.venv || (echo  Could not create a Python environment. & pause & exit /b 1)
  )
  backend\.venv\Scripts\python -m pip install --upgrade pip --disable-pip-version-check
  backend\.venv\Scripts\python -m pip install -r backend\requirements.txt --disable-pip-version-check || (echo  Backend install failed, see the error above. & pause & exit /b 1)
  echo done> backend\.venv\installed-v1.txt
  findstr /b /c:"STORE=atlas" backend\.env >nul
  if not errorlevel 1 (
    echo  Creating the Atlas Search and Vector Search indexes...
    pushd backend
    .venv\Scripts\python -m scripts.create_indexes
    popd
  )
)

rem ---- Frontend setup (first run only) ------------------------------------------
if not exist frontend\node_modules (
  echo.
  echo  First run: installing the frontend...
  pushd frontend
  call npm install --no-audit --no-fund || (popd & echo  Frontend install failed. & pause & exit /b 1)
  popd
)

echo.
echo  Starting... two windows will open. Keep them open while you use the app.
start "Spelunk backend" cmd /k "cd /d %~dp0backend && .venv\Scripts\python -m uvicorn app.main:app --port 8000"
start "Spelunk frontend" cmd /k "cd /d %~dp0frontend && npm run dev -- --port 5173"
timeout /t 10 /nobreak >nul
start "" http://localhost:5173
echo.
echo  Opened http://localhost:5173
echo  No repo yet? Paste https://github.com/morluto/rea into "Index a GitHub repository",
echo  or run index-rea.bat (faster, shows progress).
echo.
pause
