@echo off
chcp 65001 >nul
title Lubiao AI Service
cd /d "%~dp0"
echo Starting Lubiao AI service...
echo Open http://127.0.0.1:8787 in your browser
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8787"
python server.py
pause
