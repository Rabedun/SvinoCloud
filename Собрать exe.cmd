@echo off
chcp 65001 >nul
title Собрать Свиноклауд в exe

rem  Собирает Windows-версию плеера одним файлом dist\Свиноклауд\Свиноклауд.exe
rem  Понадобится Python + PyInstaller:  pip install pyinstaller
rem  yt-dlp.exe (17 МБ) должен лежать рядом с этим скриптом — плеер кладёт его
rem  внутрь сборки, чтобы в exe работали SoundCloud/YouTube.

cd /d "%~dp0"

if not exist "yt-dlp.exe" (
    echo [ОШИБКА] Не найден yt-dlp.exe рядом со скриптом.
    echo Скачай его с https://github.com/yt-dlp/yt-dlp/releases и положи сюда.
    pause
    exit /b 1
)

echo === Проверяю PyInstaller ===
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller не установлен, ставлю...
    python -m pip install pyinstaller
)

echo === Собираю (займёт 3-7 минут) ===
python -m PyInstaller Svinoklaud.spec --noconfirm --clean

if not exist "dist\Свиноклауд\Свиноклауд.exe" (
    echo.
    echo [ОШИБКА] Сборка не удалась — смотри сообщения выше.
    pause
    exit /b 1
)

echo.
echo === Готово! Плеер собран: dist\Свиноклауд\Свиноклауд.exe ===
echo Папку dist\Свиноклауд можно запаковать в zip и раздавать.
pause