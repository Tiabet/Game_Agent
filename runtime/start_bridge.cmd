@echo off
setlocal
cd /d "%~dp0.."
"%CD%\.venv\Scripts\python.exe" planner_bridge.py --watch --bridge opencode --opencode-cmd "C:\Users\ailab\AppData\Roaming\npm\opencode.cmd" --opencode-model openai/gpt-5.5-fast --opencode-timeout 120 > "runtime\bridge_direct.log" 2>&1
