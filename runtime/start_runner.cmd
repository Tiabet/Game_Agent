@echo off
setlocal
cd /d "%~dp0.."
"%CD%\.venv\Scripts\python.exe" -m explorer.runner --planner external --iterations 0 --wait-for-response --response-timeout 130 --clear-response-after-use --execute --direct-vision --korean-ocr > "runtime\runner_direct.log" 2>&1
