@echo off
chcp 65001 >nul
cd /d "%~dp0"
title СвязьКлауд
rem ============================================================================
rem  Запуск плеера на том Python, где установлен Whisper.
rem  На компьютере бывает несколько Python, и обычный запуск по двойному клику
rem  иногда выбирает не тот — тогда текст песни «недоступен».
rem ============================================================================

set "GOODPY="
set "PYLOCAL=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"

if exist "%PYLOCAL%" (
  "%PYLOCAL%" -c "import whisper" >nul 2>&1
  if not errorlevel 1 set "GOODPY=%PYLOCAL%"
)

if not defined GOODPY (
  echo Whisper не найден. Устанавливаю, это займёт несколько минут...
  if exist "%PYLOCAL%" (
    "%PYLOCAL%" -m pip install openai-whisper imageio-ffmpeg
    if not errorlevel 1 set "GOODPY=%PYLOCAL%"
  )
)

if not defined GOODPY (
  echo Не удалось установить Whisper.
  echo Нажмите правую кнопку на этом файле и выберите "Запуск от имени администратора".
  pause
  exit /b 1
)

echo Запускаю плеер на %GOODPY%
rem Библиотека vkaudiotoken нужна только для автополучения токена ВК.
rem Без неё всё остальное работает, а ВК берётся токеном вручную.
"%GOODPY%" -c "import vkaudiotoken" >nul 2>&1
if errorlevel 1 (
  echo Устанавливаю vkaudiotoken для получения токена ВК...
  "%GOODPY%" -m pip install vkaudiotoken --quiet
)
echo Чтобы закрыть плеер, закройте это окно.
"%GOODPY%" main.py
if errorlevel 1 pause
