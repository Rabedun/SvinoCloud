"""Обновления Свиноклауда: проверка новой версии и установка её из плеера.

Плеер лежит на GitHub, и автор время от времени выпускает новые версии. Этот
модуль делает две вещи:

  1. Спрашивает у GitHub, какая версия последняя, и сравнивает с текущей.
  2. Скачивает выпуск (zip-архив с плеером) и готовит замену файлов, чтобы
     пользователю хватило одной кнопки в настройках — без ручной распаковки.

Файлы пользователя при обновлении не затираются: плейлист, оформление и токены
переживают обновление (см. PROTECTED_FILES).

Заменить файлы работающего плеера напрямую — плохая идея: на Windows exe занят
процессом, а часть файлов уже загружена в память. Поэтому обновление ставит
отдельная программа (cmd-скрипт): она ждёт выхода плеера, копирует файлы и
запускает плеер заново. Отсюда «Скачать и установить» заканчивается
перезапуском плеера — это нормально и задумано.

Всё написано на стандартной библиотеке: никаких сторонних зависимостей.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import urllib.error
import urllib.parse
import urllib.request

# Текущая версия плеера. Поднимай её при каждом выпуске — от неё считается,
# есть ли на GitHub что-то новее. Тег выпуска на GitHub должен быть v<версия>.
APP_VERSION = "1.2.0"

# Репозиторий на GitHub в формате «владелец/название». Свой адрес можно задать
# переменной окружения SVINOKLAUD_REPO, не меняя код (удобно для форков).
GITHUB_REPO = os.environ.get("SVINOKLAUD_REPO", "SvyazKlaud/SvyazCloud").strip() \
    or "SvyazKlaud/SvyazCloud"

# Страница последнего выпуска — куда отправляем пользователя за обновлением.
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"

# Открытый адрес GitHub API: отдаёт JSON про последний выпуск (release).
# {repo} подставляется автоматически.
RELEASES_API = "https://api.github.com/repos/{repo}/releases/latest"

# Файлы пользователя: при обновлении НЕ затираются, иначе после обновления
# пропал бы плейлист, оформление и сохранённые токены ВК и Яндекса.
PROTECTED_FILES = frozenset({
    "playlist.json", "theme_config.json", "vk_token.txt", "ym_token.txt",
})

# Что не тащим в архив и не ставим при обновлении: служебные папки.
SKIP_DIRS = frozenset({"__pycache__", ".git", ".github", ".idea", ".vs"})

# Что не тащим в архив и не ставим при обновлении: мусорные хвосты файлов.
SKIP_SUFFIXES = (".pyc", ".pyo", ".part", ".zip", ".log")

# Предохранитель: архив плеера — единицы мегабайт, но на случай опечатки в
# выпуске (например, туда попадёт видео) качать больше 300 МБ не будем.
MAX_DOWNLOAD = 300 * 1024 * 1024

# Куда кладём готовое обновление и команду перезапуска. Папка временная, но не
# привязанная к процессу: плеер перезапустится, а обновление должно дождаться.
PENDING_FILE = os.path.join(tempfile.gettempdir(),
                            "svinoklaud_pending_update.json")

# Имя скрипта, который дожидается выхода плеера и подменяет файлы.
RESTART_SCRIPT = "_update.cmd"

# Имя файла запуска плеера (двойным кликом). Если он есть — перезапускаем им,
# чтобы снова выбрался правильный Python с Whisper.
LAUNCHER_NAME = "Запустить плеер.cmd"


def app_dir():
    """Папка, где лежит плеер: main.py, настройки и всё остальное."""
    if getattr(sys, "frozen", False):
        # Собранный в exe (PyInstaller) — рядом с exe, а не во временной папке.
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _repo(repo):
    """Репозиторий для запроса: переданный явно, из окружения или по умолчанию."""
    if repo:
        return repo
    return GITHUB_REPO


def _headers():
    """Заголовки запроса к GitHub: без User-Agent API отвечает отказом."""
    return {
        "User-Agent": "Svinoklaud-Updater/" + APP_VERSION,
        "Accept": "application/vnd.github+json",
    }


def parse_version(text):
    """Превращает строку версии в кортеж чисел для сравнения.

    «v1.2.3», «1.2», «version 1.2.3-beta» -> (1, 2, 3). Нечисловой хвост и
    лишние слова отбрасываются, короткие версии дополняются нулями до трёх
    чисел — чтобы (1, 2) и (1, 2, 0) сравнивались как равные.
    """
    nums = re.findall(r"\d+", str(text or ""))
    parts = [int(n) for n in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def has_update(current, latest):
    """True, если latest новее current. Равенство и «откат» — не обновление."""
    return parse_version(latest) > parse_version(current)


def human_size(num):
    """Размер в человеческом виде: 1536 -> «1.5 КБ» (для статуса и вопроса)."""
    size = float(num or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{int(size)} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def _releases_url(repo):
    """Готовый адрес запроса для конкретного репозитория."""
    return RELEASES_API.format(repo=_repo(repo))



def latest_release(repo=None, timeout=10, opener=None):
    """Сырые данные последнего выпуска GitHub. Возвращает (словарь, ошибка).

    opener — подставная функция для тестов: вызывается как opener(url, timeout)
    и должна вернуть JSON строкой. Сеть она не трогает.
    """
    url = _releases_url(repo)
    try:
        if opener is not None:
            payload = opener(url, timeout)
        else:
            request = urllib.request.Request(url, headers=_headers())
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", "replace")
        data = json.loads(payload) if isinstance(payload, str) else payload
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}, "На GitHub ещё нет ни одного выпуска"
        return {}, f"GitHub ответил ошибкой {exc.code}"
    except Exception as exc:  # noqa: BLE001 — сеть ненадёжна, гасим всё
        return {}, f"Не получилось связаться с GitHub: {exc}"
    if not isinstance(data, dict):
        return {}, "GitHub вернул непонятный ответ"
    return data, ""


def pick_asset(release):
    """Выбирает файл для скачивания из выпуска. Возвращает {} если нечего брать.

    Порядок выбора:
      1. Установщик (.exe/.msi) — он сам всё расставит, плееру не надо копировать;
      2. zip-архив с плеером — распаковываем и копируем файлы;
      3. Исходники выпуска (zipball_url) — то же, что архив, но от самого GitHub.
    """
    if not isinstance(release, dict):
        return {}
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    # Сначала установщики: если автор собрал exe, ручное копирование не нужно.
    for asset in assets:
        name = str(asset.get("name") or "").lower()
        url = str(asset.get("browser_download_url") or "")
        if url and name.endswith((".exe", ".msi")):
            return {"url": url, "name": str(asset.get("name") or "setup"),
                    "size": int(asset.get("size") or 0), "installer": True}
    # Потом архивы: предпочитаем те, где в имени есть «свиноклауд/svinoklaud».
    # Проверяем только, что адрес вообще есть: он может быть и не http
    # (например, file:// — так удобно проверять установку вручную).
    archives = [a for a in assets
                if str(a.get("name") or "").lower().endswith(".zip")
                and str(a.get("browser_download_url") or "")]
    tagged = [a for a in archives
              if any(w in str(a.get("name") or "").lower()
                     for w in ("svinoklaud", "свиноклауд", "player", "плеер"))]
    chosen = (tagged or archives or [None])[0]
    if chosen:
        return {"url": str(chosen.get("browser_download_url")),
                "name": str(chosen.get("name") or "Svinoklaud.zip"),
                "size": int(chosen.get("size") or 0), "installer": False}
    # Последний вариант — исходники выпуска прямо от GitHub.
    zipball = str(release.get("zipball_url") or "")
    if zipball.startswith("http"):
        return {"url": zipball, "name": "source.zip", "size": 0, "installer": False}
    return {}


def check_for_updates(current=None, repo=None, timeout=10, opener=None):
    """Спрашивает GitHub про последнюю версию. Всегда возвращает словарь.

    Никогда не бросает исключений: любая осечка (нет сети, GitHub недоступен,
    ответ не тот) превращается в ok=False с понятной ошибкой. Ключи ответа:
      ok         — удалось ли получить ответ;
      has_update — есть ли более новая версия;
      current    — версия, что запущена сейчас;
      latest     — последняя версия на GitHub;
      url        — страница выпуска (куда отправить пользователя);
      notes      — краткое описание изменений;
      asset      — файл для скачивания ({} если взять нечего);
      error      — текст ошибки, если ok=False.
    """
    current = current or APP_VERSION
    result = {
        "ok": False, "has_update": False, "current": current,
        "latest": "", "url": "", "notes": "", "asset": {}, "error": "",
    }
    data, error = latest_release(repo=repo, timeout=timeout, opener=opener)
    if error:
        result["error"] = error
        return result
    latest = str(data.get("tag_name") or "").strip()
    if not latest:
        result["error"] = "GitHub не сообщил номер версии"
        return result
    result["ok"] = True
    result["latest"] = latest
    result["url"] = str(data.get("html_url")
                        or RELEASES_PAGE.format(repo=_repo(repo)))
    body = data.get("body") or data.get("name") or ""
    result["notes"] = " ".join(str(body).split())
    result["asset"] = pick_asset(data)
    result["has_update"] = has_update(current, latest)
    return result

    nums = re.findall(r"\d+", str(text or ""))
    parts = [int(n) for n in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)




# === Скачивание и установка обновления =======================================

def _safe_rel(name):
    """Приводит имя файла из архива к безопасному относительному пути.

    Архив — данные из интернета, поэтому «../secret» и абсолютные адреса
    отбрасываем: распаковка не должна ничего писать вне папки плеера.
    """
    clean = str(name or "").replace("\\", "/").lstrip("/")
    parts = [p for p in clean.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return ""
    return "/".join(parts)


def zip_root(names):
    """Общая папка внутри архива или "" — если файлы лежат в корне.

    GitHub кладёт исходники выпуска в папку вида «Владелец-Репозиторий-хеш»,
    а собранный вручную архив — обычно сразу с файлами. Снимаем эту лишнюю
    папку, чтобы main.py лёг рядом с main.py, а не во вложенную директорию.
    """
    tops = set()
    for name in names:
        rel = _safe_rel(name)
        if not rel:
            continue
        if "/" not in rel:
            return ""            # в корне есть файл — общей папки нет
        tops.add(rel.split("/")[0])
    return tops.pop() if len(tops) == 1 else ""


def plan_files(zip_path):
    """Список (имя в архиве, путь у пользователя) для установки.

    Служебные папки и файлы пользователя из плана исключаются: плейлист и
    оформление должны пережить обновление, а __pycache__ только мешает.
    """
    plan = []
    with zipfile.ZipFile(zip_path) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        prefix = zip_root(names)
        for name in names:
            rel = _safe_rel(name)
            if not rel:
                continue
            if prefix:
                if not rel.startswith(prefix + "/"):
                    continue        # файл вне общей папки — не наш
                rel = rel[len(prefix) + 1:]
            if not rel:
                continue
            if any(part in SKIP_DIRS for part in rel.split("/")):
                continue
            if os.path.basename(rel) in PROTECTED_FILES:
                continue
            if rel.lower().endswith(SKIP_SUFFIXES):
                continue
            plan.append((name, rel))
    return plan


def stage_update(zip_path, stage_dir):
    """Распаковывает нужные файлы архива в отдельную папку. Список путей.

    Ставим не из архива сразу, а через промежуточную папку: сначала проверяем,
    что распаковка удалась, и только потом отдаём команду на подмену файлов.
    """
    os.makedirs(stage_dir, exist_ok=True)
    staged = []
    with zipfile.ZipFile(zip_path) as zf:
        for name, rel in plan_files(zip_path):
            info = zf.getinfo(name)
            if info.file_size > MAX_DOWNLOAD:
                raise OSError(f"в архиве слишком большой файл: {rel}")
            target = os.path.join(stage_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
            staged.append(rel)
    return staged


def download(url, dest, timeout=120, progress=None):
    """Качает файл по адресу в dest. Возвращает размер или бросает OSError.

    Качаем потоком: архив плеера весит единицы мегабайт, читать его целиком в
    память незачем. progress(скачано, всего) зовём после каждого куска, чтобы
    плеер мог показать проценты (всего=0 — сервер не сказал размер).
    """
    request = urllib.request.Request(
        url, headers={**_headers(),
                      "Accept": "application/octet-stream,*/*;q=0.8"})
    # Адрес file:// читаем напрямую с диска: так удобно проверить установку
    # вручную, не выкладывая архив в интернет.
    if str(url).lower().startswith("file:"):
        # Windows-путь в file:// выглядит как file:///C:/папка/файл — но люди
        # пишут и file://C:/…. Разбираем сами: срезаем схему и начальные слэши,
        # а проценты (%D0%9F) превращаем в буквы.
        raw = str(url)
        raw = raw.split("://", 1)[-1] if "://" in raw else raw[len("file:"):]
        raw = raw.lstrip("/")
        src = urllib.parse.unquote(raw).replace("/", os.sep)
        if not os.path.isfile(src):
            raise OSError("нет файла обновления на диске")
        # copyfile возвращает путь, а размер берём с диска: вызывающему коду
        # нужен именно размер (он показывает проценты и пишет в очередь).
        shutil.copyfile(src, dest)
        return os.path.getsize(dest)
    total = 0
    done = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and str(length).isdigit():
            total = int(length)
        if total > MAX_DOWNLOAD:
            raise OSError(f"файл слишком большой: {human_size(total)}")
        with open(dest, "wb") as handle:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                done += len(chunk)
                if done > MAX_DOWNLOAD:
                    raise OSError("файл больше объявленного — скачивание прервано")
                handle.write(chunk)
                if progress is not None:
                    progress(done, total)
    if done <= 0:
        raise OSError("сервер отдал пустой файл")
    return done


def _rmtree(path):
    """Удаляет папку со всем содержимым, не жалуясь на занятые файлы."""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _write_script(path, text):
    """Пишет cmd-скрипт в UTF-8: в нём русский текст и русские пути."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _restart_lines(dest):
    """Строки скрипта, которые запускают плеер заново после обновления."""
    launcher = os.path.join(dest, LAUNCHER_NAME)
    if os.path.exists(launcher):
        # Через файл запуска — он сам выберет Python с Whisper.
        return f'cd /d "{dest}"\nstart "" "{LAUNCHER_NAME}"'
    # Файла запуска нет (запускали python main.py напрямую) — так и вернём.
    return (f'cd /d "{dest}"\nstart "" "{sys.executable}" '
            f'"{os.path.join(dest, "main.py")}"')


def _wait_and_copy_script(stage, dest, pid):
    """cmd-скрипт: ждёт выхода плеера, копирует файлы, запускает плеер.

    Пишем именно скриптом, а не копированием из самого плеера: пока плеер
    работает, часть файлов занята процессом, и замена может не удастись.
    Скрипт стартует сразу (в своём окне консоли), но копирование начнёт
    только когда плеер закроется.
    """
    log = os.path.join(os.path.dirname(stage), "_update.log")
    return f"""@echo off
chcp 65001 >nul
rem Обновление Свиноклауда. Создано плеером, удаляет себя само.
set "SRC={stage}"
set "DST={dest}"
set "LOG={log}"
set "WAITPID={pid}"
title Обновление Свиноклауда
echo Жду, пока плеер закроется...
:wait
tasklist /fi "PID eq %WAITPID%" 2>nul | find "%WAITPID%" >nul
if not errorlevel 1 (
  timeout /t 1 /nobreak >nul
  goto wait
)
timeout /t 2 /nobreak >nul
echo Копирую новые файлы...
xcopy /E /Y /Q /I /H /R "%SRC%\\*" "%DST%\\" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Не получилось заменить файлы — обновление отменено.
  echo Подробности записаны в %LOG%
  echo Можно просто скачать новую версию с GitHub вручную.
  timeout /t 30 /nobreak >nul
  exit /b 1
)
del /q "{PENDING_FILE}" >nul 2>&1
rmdir /s /q "%SRC%" >nul 2>&1
del /q "%~f0" >nul 2>&1
echo Готово. Запускаю Свиноклауд...
{_restart_lines(dest)}
"""


def _run_installer_script(installer):
    """cmd-скрипт для случая, когда в выпуске лежит готовый установщик .exe."""
    log = os.path.join(os.path.dirname(installer), "_update.log")
    return f"""@echo off
chcp 65001 >nul
rem Обновление Свиноклауда: запускаем установщик из выпуска. Создано плеером.
set "EXE={installer}"
set "LOG={log}"
title Обновление Свиноклауда
del /q "{PENDING_FILE}" >nul 2>&1
echo Запускаю установщик обновления — следуйте его указаниям...
start "" /wait "%EXE%" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo Установщик завершился с ошибкой — код %errorlevel%.
  echo Подробности записаны в %LOG%
  timeout /t 30 /nobreak >nul
  exit /b 1
)
echo Установщик отработал. Убираю за собой...
del /q "%EXE%" >nul 2>&1
del /q "%~f0" >nul 2>&1
"""


def prepare_update(info, progress=None, timeout=120, pid=None):
    """Скачивает выпуск и готовит скрипт установки. Возвращает (словарь, ошибка).

    Сам плеер при этом НЕ меняется — файлы только качаются и распаковываются во
    временную папку. Подмена делается скриптом после launch_pending(). Ключи:
      dir       — папка с готовым обновлением (её и подменит скрипт);
      script    — файл скрипта установки;
      files     — сколько файлов будет обновлено (0 для установщика);
      size      — размер скачанного архива в байтах;
      latest    — версия, которую ставим;
      installer — True, если ставим готовым установщиком .exe.
    """
    asset = (info or {}).get("asset") or {}
    url = str(asset.get("url") or "")
    if not url:
        return {}, "В выпуске нет файла для скачивания — обновите вручную"
    work = tempfile.mkdtemp(prefix="svinoklaud_update_")
    archive = os.path.join(work, "update.zip")
    try:
        size = download(url, archive, timeout=timeout, progress=progress)
    except Exception as exc:  # noqa: BLE001 — сеть ненадёжна, гасим всё
        _rmtree(work)
        return {}, f"Не удалось скачать обновление: {exc}"

    if asset.get("installer"):
        # Установщик сам разберётся: отдаём ему файл и ждём.
        exe = os.path.join(work, os.path.basename(asset.get("name") or "setup.exe"))
        if exe.lower() != archive.lower():
            try:
                os.replace(archive, exe)
            except OSError:
                exe = archive
        script = _write_script(os.path.join(work, RESTART_SCRIPT),
                               _run_installer_script(exe))
        result = {"dir": work, "script": script, "files": 0, "size": size,
                  "latest": info.get("latest", ""), "installer": True}
        save_pending(result)
        return result, ""

    stage = os.path.join(work, "stage")
    try:
        files = stage_update(archive, stage)
    except Exception as exc:  # noqa: BLE001 — битый архив не должен ронять плеер
        _rmtree(work)
        return {}, f"Архив обновления повреждён: {exc}"
    if not files:
        _rmtree(work)
        return {}, "В архиве нет файлов плеера — обновите вручную"
    if not any(rel.endswith("main.py") for rel in files):
        _rmtree(work)
        return {}, "В архиве нет main.py — это не обновление плеера"
    try:
        os.remove(archive)
    except OSError:
        pass
    script = _write_script(os.path.join(work, RESTART_SCRIPT),
                           _wait_and_copy_script(stage, app_dir(),
                                                 pid or os.getpid()))
    result = {"dir": work, "script": script, "files": len(files), "size": size,
              "latest": info.get("latest", ""), "installer": False}
    save_pending(result)
    return result, ""


def save_pending(data):
    """Запоминает готовое обновление, чтобы применить его при выходе."""
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except OSError:
        return False
    return True


def load_pending():
    """Читает готовое обновление. Возвращает {} если его нет или оно негодное."""
    try:
        with open(PENDING_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Папка и скрипт должны быть на месте, иначе обновление уже не применить.
    if not os.path.isdir(str(data.get("dir") or "")):
        return {}
    if not os.path.isfile(str(data.get("script") or "")):
        return {}
    return data


def clear_pending():
    """Забывает готовое обновление (оно устарело или уже применено)."""
    data = load_pending()
    if data:
        _rmtree(str(data.get("dir") or ""))
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass


def launch_pending():
    """Запускает скрипт установки. True если он стартовал.

    Скрипт получает отдельное окно консоли и живёт независимо от плеера: он
    дождётся нашего выхода и только потом тронет файлы.
    """
    data = load_pending()
    script = str(data.get("script") or "")
    if not script:
        return False
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/c", script],
                     cwd=str(data.get("dir") or os.path.dirname(script)),
                     creationflags=flags)
    return True

