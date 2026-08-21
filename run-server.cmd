@echo off
setlocal
cd /d "%~dp0"
set "RESEARCH_CLI=%~dp0data\research\AnimeStudio\AnimeStudio.CLI\bin\Release\net8.0-windows\AnimeStudio.CLI.exe"
if exist "%RESEARCH_CLI%" set "VFS_BROWSER_ANIMESTUDIO_CLI=%RESEARCH_CLI%"
python -u server.py --db "%~dp0data\endfield-vfs-index.sqlite" --host 0.0.0.0 --port 8765 >> server.log 2>> server.err
