@echo off
rem Starts DuTemps on http://127.0.0.1:8765 and opens the browser.
rem Close this window (or press Ctrl+C) to stop it.
cd /d "%~dp0"
python -m dutemps serve %*
