@echo off
rem Penguin's Storyboard setup for Windows (experimental). Double-click this file.
rem It runs windows\setup.ps1, which installs what the app needs. The README says what that is.
rem The .command files next to this one do the same on a Mac.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\setup.ps1" %*
set "CODE=%ERRORLEVEL%"
if not defined STORYBOARD_NO_PAUSE pause
exit /b %CODE%
