@echo off
title Golden Finds - installer
echo.
echo   Installing Golden Finds. This takes a few minutes the first time.
echo   Keep this window open until it says it is finished.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0golden-finds-pos\windows\install.ps1"
echo.
pause
