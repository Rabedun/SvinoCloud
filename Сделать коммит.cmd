@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Первый коммит СвязьКлауд
rem ============================================================================
rem  Один клик вместо трёх команд git. Делает то же самое:
rem    создаёт репозиторий (если его нет), добавляет файлы и сохраняет коммит.
rem  Проверено: в списке файлов обязательно lyrics_web.py — без него
rem  main.py не запустится, потому что в самом верху идёт import lyrics_web.
rem ============================================================================

where git >nul 2>&1
if errorlevel 1 (
  echo Git не установлен. Установите его и запустите этот файл ещё раз:
  echo.
  echo   winget install --id Git.Git -e --source winget
  echo.
  echo Ссылка, если winget нет: https://git-scm.com/download/win
  pause
  exit /b 1
)

if not exist ".git" (
  echo Создаю новый репозиторий...
  git init
)

rem Имя и почта нужны для коммита; задаём локально, чтобы не спотыкаться
git config user.name >nul 2>&1
if errorlevel 1 git config user.name "SvyazKlaud"

git config user.email >nul 2>&1
if errorlevel 1 git config user.email "svyazklaud@localhost"

echo.
echo Добавляю файлы...
git add main.py
git add updater.py
git add lyrics_web.py
git add vocals.py
git add "Запустить плеер.cmd"
git add .gitignore
git add test_playlist_search.py
git add test_whisper_smoke.py
git add test_lyrics_scroll.py
git add test_whisper_live.py
git add test_platforms_theme.py
git add "Сделать коммит.cmd"

echo.
echo Сохраняю коммит...
git commit -m "Импорт плейлиста Яндекс.Музыки по новой ссылке /playlists/<uuid>"

echo.
echo Что попало в коммит:
git --no-pager log --oneline -1
git --no-pager status --short
echo.
echo Готово. Теперь случайную правку можно вернуть: git checkout -- main.py
pause
