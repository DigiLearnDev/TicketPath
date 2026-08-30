@echo off
cd /d "%~dp0"
python generate_diagram.py
if errorlevel 1 (
    echo.
    echo Chyba pri generovani diagramu. Zkontroluj tickets.txt.
    pause
)
