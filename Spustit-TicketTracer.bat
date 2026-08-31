@echo off
cd /d "%~dp0"
start "TicketTracer server" python server.py
timeout /t 1 >nul
start "" http://localhost:8765
