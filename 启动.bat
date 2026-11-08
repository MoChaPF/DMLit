@echo off
cd /d "%~dp0"
set LOG=%~dp0bat.log
echo ==== %date% %time% ==== > "%LOG%"
set PY=C:\Users\wangz\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
echo PY=%PY% >> "%LOG%"
if not exist "%PY%" (
  echo 找不到 Python 运行时：%PY% >> "%LOG%"
  pause
  exit /b 1
)
"%PY%" app.py >> "%LOG%" 2>&1
echo [exit code = %errorlevel%] >> "%LOG%"
pause
