@echo off
rem Retrieval eval on REA: hit@5 and MRR for keyword vs semantic vs hybrid.
cd /d "%~dp0backend"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python -m scripts.eval --k 5 --boost 1.5 --verbose > ..\eval_results.txt 2>&1
type ..\eval_results.txt
echo.
pause
