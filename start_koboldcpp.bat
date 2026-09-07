@echo off
title KoboldCPP - AI Server untuk OJS Pipeline
color 0A

echo ============================================
echo   KoboldCPP AI Server - OJS Pipeline
echo ============================================
echo.
echo [INFO] Menjalankan KoboldCPP dengan model Mistral 7B...
echo [INFO] Server akan berjalan di: http://localhost:5001
echo [INFO] Tekan Ctrl+C untuk menghentikan server
echo.

set KOBOLD_EXE=C:\Users\Lala\Downloads\koboldcpp.exe
set MODEL_PATH=C:\Users\Lala\Downloads\mistral-7b-instruct-v0.3-q4_k_m.gguf

"%KOBOLD_EXE%" --model "%MODEL_PATH%" --port 5001 --contextsize 4096 --threads 4 --noshift --skiplauncher

echo.
echo [INFO] Server berhenti.
pause
