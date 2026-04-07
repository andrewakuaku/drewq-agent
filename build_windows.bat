@echo off
REM Build the DREWQ Reader Agent for Windows
REM Run: build_windows.bat
REM Output: dist\DREWQ Reader.exe

cd /d "%~dp0"

echo =^> Installing dependencies...
pip install -r requirements.txt pyinstaller

echo =^> Generating placeholder icons...
python -c "from PIL import Image, ImageDraw; import os; os.makedirs('assets', exist_ok=True); img=Image.new('RGBA',(256,256),(0,0,0,0)); d=ImageDraw.Draw(img); d.ellipse([4,4,252,252],fill='#1a1a1a'); d.ellipse([64,64,192,192],fill='#22c55e'); img.save('assets/icon.ico'); img.save('assets/icon.png'); print('Icons generated.')"

echo =^> Building with PyInstaller...
pyinstaller drewq.spec --clean --noconfirm

echo.
echo Build complete.
echo   Windows: dist\DREWQ Reader.exe
echo.
pause
