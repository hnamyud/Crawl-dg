@echo off
cd /d "%~dp0"
if not exist ".venv-build\Scripts\python.exe" (
  py -3.12 -m venv .venv-build
)
.venv-build\Scripts\python.exe -m pip install --upgrade pip
.venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onefile --name DGTSCrawler --runtime-hook pyinstaller_runtime_hook.py --hidden-import pyexpat --collect-all tkcalendar launch_ui.py
