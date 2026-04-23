@echo off
echo ========================================================
echo  Instalando dependencias - Auditor ONPE 2026
echo ========================================================

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python no encontrado. Instala Python 3.10+ desde https://python.org
    pause & exit /b 1
)

:: Verificar Tesseract
if not exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo ADVERTENCIA: Tesseract no encontrado en la ruta esperada.
    echo  Descargalo de: https://github.com/UB-Mannheim/tesseract/wiki
    echo  Instala el paquete de idioma "spa" ^(Spanish^)
    echo.
)

:: Instalar dependencias Python
echo Instalando paquetes Python...
pip install -r requirements.txt

if errorlevel 1 (
    echo ERROR al instalar dependencias.
    pause & exit /b 1
)

:: Crear carpetas de datos
if not exist "data\actas"   mkdir data\actas
if not exist "data\ocr"     mkdir data\ocr
if not exist "data\reports" mkdir data\reports

echo.
echo ========================================================
echo  Instalacion completada correctamente.
echo  Ejecuta run.bat para iniciar la aplicacion.
echo ========================================================
pause
