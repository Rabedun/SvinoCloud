@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Свиноклауд — сборка архива для выпуска
rem ============================================================================
rem  Собирает zip-архив плеера (Svinoklaud.zip) для прикладывания к релизу
rem  на GitHub. Берёт только нужные файлы, мусор (.sc_cache, __pycache__,
rem  логи, личные данные) НЕ попадает в архив.
rem ============================================================================

set "OUT=Svinoklaud.zip"
if exist "%OUT%" del "%OUT%"

rem Файлы плеера, которые должны быть в архиве.
set "FILES=main.py updater.py lyrics_web.py vocals.py requirements.txt README.md .gitignore \"Запустить плеер.cmd\" \"Сделать коммит.cmd\" \"Собрать релиз.cmd\" test_playlist_search.py test_whisper_smoke.py test_lyrics_scroll.py test_whisper_live.py test_platforms_theme.py"

echo Собираю %OUT% ...
powershell -NoProfile -Command "Compress-Archive -Path %FILES% -DestinationPath '%OUT%' -Force"

if not exist "%OUT%" (
  echo.
  echo Не удалось собрать архив. Соберите его вручную: выделите файлы
  echo плеера, правый клик - "Сжать в ZIP-файл".
  pause
  exit /b 1
)

echo.
echo Готово: %OUT%
echo Приложите этот файл к новому выпуску (Release) на GitHub.
echo.
pause
