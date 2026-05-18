@echo off
setlocal

set VENV_PYTHON=%~dp0venv\Scripts\python.exe
set BUILD_DIR=C:\ResearchForge_build

echo === ResearchForge — Nuitka Build (single exe) ===
echo.

if not exist "%VENV_PYTHON%" (
    echo ERROR: venv not found at %VENV_PYTHON%
if not defined RELEASE_MODE pause
    exit /b 1
)

echo --- MSVC Detection ---
if exist "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" (
    "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -latest -property installationVersion
    "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -latest -property productLineVersion
) else (
    echo   WARNING: No Visual Studio found -- Nuitka will fall back to MinGW64
)
echo.

echo Cleaning previous build...
if exist "%BUILD_DIR%\ResearchForge.build" rmdir /s /q "%BUILD_DIR%\ResearchForge.build"
if exist "%BUILD_DIR%\ResearchForge.dist" rmdir /s /q "%BUILD_DIR%\ResearchForge.dist"
if exist "%BUILD_DIR%\ResearchForge.onefile-build" rmdir /s /q "%BUILD_DIR%\ResearchForge.onefile-build"

echo.
echo Starting Nuitka compilation...
echo Output: %BUILD_DIR%
echo.
echo Activating MSVC 2022 ...
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
echo.

"%VENV_PYTHON%" -m nuitka ^
  --onefile ^
  --enable-plugin=pyside6 ^
  --output-dir=%BUILD_DIR% ^
  --output-filename=ResearchForge.exe ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=design\app_icone.ico ^
  --include-data-dir=design=design ^
  --include-data-dir=config=config ^
  --include-data-dir=themes=themes ^
  --include-module=ddgs ^
  --include-module=pypdf ^
  --include-module=fitz ^
  --include-module=arxiv ^
  --include-module=tqdm ^
  --include-module=requests ^
  --nofollow-import-to=PySide6.QtWebEngine ^
  --nofollow-import-to=PySide6.QtWebEngineCore ^
  --nofollow-import-to=PySide6.QtWebEngineWidgets ^
  --nofollow-import-to=PySide6.QtNetwork ^
  --nofollow-import-to=PySide6.QtOpenGL ^
  --nofollow-import-to=PySide6.QtOpenGLWidgets ^
  --nofollow-import-to=PySide6.QtQml ^
  --nofollow-import-to=PySide6.QtQuick ^
  --nofollow-import-to=PySide6.QtQuickWidgets ^
  --nofollow-import-to=PySide6.QtMultimedia ^
  --nofollow-import-to=PySide6.QtMultimediaWidgets ^
  --nofollow-import-to=PySide6.QtSql ^
  --nofollow-import-to=PySide6.QtXml ^
  --nofollow-import-to=PySide6.QtBluetooth ^
  --nofollow-import-to=PySide6.QtPositioning ^
  --nofollow-import-to=PySide6.QtSensors ^
  --nofollow-import-to=PySide6.QtTest ^
  --nofollow-import-to=PySide6.QtXmlPatterns ^
  --nofollow-import-to=tkinter ^
  --nofollow-import-to=unittest ^
  --nofollow-import-to=test ^
  --nofollow-import-to=setuptools ^
  --nofollow-import-to=distutils ^
  --nofollow-import-to=pip ^
  --nofollow-import-to=numpy ^
  --nofollow-import-to=pandas ^
  --nofollow-import-to=scipy ^
  --nofollow-import-to=PIL ^
  --nofollow-import-to=matplotlib ^
  --nofollow-import-to=cv2 ^
  --nofollow-import-to=tensorflow ^
  --nofollow-import-to=torch ^
  --nofollow-import-to=IPython ^
  --nofollow-import-to=jupyter ^
  --nofollow-import-to=notebook ^
  --assume-yes-for-downloads ^
  --follow-imports ^
  --lto=yes ^
  --jobs=10 ^
  main.py

echo.
if %ERRORLEVEL% EQU 0 (
    echo === BUILD SUCCESS ===
    echo Executable: %BUILD_DIR%\ResearchForge.exe
    for %%A in ("%BUILD_DIR%\ResearchForge.exe" 2^>nul) do echo Size: %%~zA bytes
) else (
    echo === BUILD FAILED ===
)

pause
