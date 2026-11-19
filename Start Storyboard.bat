@echo off
rem Double-click this file to start Penguin's Storyboard on Windows (experimental).
rem It opens in your browser at http://127.0.0.1:8777 and starts the picture and video engine by itself.
rem Leave this window open while you use the app. To stop it, close this window or press Ctrl+C.
rem For Night mode, leave this window open and the PC switched on. The screen may go dark; that is fine.
title Penguin's Storyboard
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\start.ps1" %*
if errorlevel 1 pause
