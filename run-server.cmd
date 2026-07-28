@echo off
setlocal
cd /d "%~dp0"
python server.py --db "%~dp0data\endfield-vfs-index.sqlite" --host 0.0.0.0 --port 8765 >> server.log 2>> server.err
