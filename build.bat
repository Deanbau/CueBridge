@echo off
REM Build CueBridge Windows exe.
REM .venv\Scripts\activate && build.bat

echo =^> Generating icon...
pip install -q pillow
python assets\create_icon.py

echo =^> Building exe...
pyinstaller --onefile --windowed --name CueBridge ^
  --collect-all nicegui ^
  --icon assets\icon.ico ^
  --add-data "assets\icon.png;assets" ^
  --add-data "assets\icon_launcher.png;assets" ^
  main.py

echo Done: dist\CueBridge.exe
