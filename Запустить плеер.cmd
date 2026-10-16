@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Свиноклауд
rem ============================================================================
rem  Универсальный запуск: подходит любому, у кого стоит Python 3.10+.
rem  Скрипт сам находит Python, доустанавливает нехватку пакетов и стартует
rem  плеер. Whisper (~2 ГБ) НЕ ставится — распознавание сейчас отключено.
rem ============================================================================

set "PY="
where py >nul 2>&1 && py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY goto nopython

echo Проверяю компоненты плеера...
%PY% -c "import PyQt6, pygame, yt_dlp" >nul 2>&1
if errorlevel 1 (
  echo Доустанавливаю PyQt6, pygame и yt-dlp — это займёт пару минут...
  %PY% -m pip install --quiet PyQt6 pygame yt-dlp
  if errorlevel 1 goto pipfail
)

rem vkaudiotoken — только для автополучения токена ВК; без него всё работает.
%PY% -c "import vkaudiotoken" >nul 2>&1
if errorlevel 1 %PY% -m pip install --quiet vkaudiotoken >nul 2>&1

echo Запускаю Свиноклауд...
%PY% main.py
exit /b 0

:nopython
echo На компьютере не найден Python 3.10+.
echo.
echo Вариант 1 — готовая версия без Python:
echo   https://github.com/Rabedun/SvinoCloud/releases
echo   (скачайте Свиноклауд-win64.zip, распакуйте и запустите Свиноклауд.exe)
echo.
echo Вариант 2 — установите Python с галочкой "Add python.exe to PATH":
echo   https://www.python.org/downloads/
echo.
pause
exit /b 1

:pipfail
echo Не получилось установить компоненты. Попробуйте вручную:
echo   %PY% -m pip install PyQt6 pygame yt-dlp
echo.
pause
exit /b 1
