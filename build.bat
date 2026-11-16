@echo off
rem Build AudioHub.exe into dist\ (PyInstaller: one windowed exe).
cd /d "%~dp0"
if not exist .venv\Scripts\pyinstaller.exe .venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconsole --onefile --name AudioHub --clean ^
  --paths . ^
  --icon tools\AudioHub.ico ^
  --add-data "audiohub\web;audiohub\web" ^
  --add-data "extension\icons\128.png;audiohub\icons" ^
  --add-data "extension;extension" ^
  --hidden-import webview.platforms.winforms ^
  build\entry.py
