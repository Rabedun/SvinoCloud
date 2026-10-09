"""Самопроверка обновлений плеера через GitHub Releases.

Плеер лежит на GitHub, и автор иногда выпускает новые версии. Этот модуль
один раз спрашивает у GitHub, какая версия последняя, и сравнивает её с той,
что запущена сейчас. Ничего сам не скачивает и не перезаписывает — только
честно сообщает пользователю, что вышло обновление, и даёт ссылку.

Всё намеренно простое и без сторонних зависимостей: плеер должен запускаться
у любого пользователя без установки «обновлялок».
"""
import json
import re
import urllib.error
import urllib.request

# Текущая версия плеера. Поднимай её при каждом выпуске — от неё считается,
# есть ли на GitHub что-то новее.
APP_VERSION = "1.0.0"

# Репозиторий на GitHub в формате «владелец/название». Свой адрес можно задать
# переменной окружения SVYAZ_REPO, не меняя код (полезно при форках).
GITHUB_REPO = "SvyazKlaud/SvyazCloud"

# Страница последнего выпуска — куда отправляем пользователя за обновлением.
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"

# Открытый адрес GitHub API: отдаёт JSON про последний выпуск (release).
# {repo} подставится автоматически.
RELEASES_API = "https://api.github.com/repos/{repo}/releases/latest"


def _repo(repo):
    """Репозиторий для запроса: переданный явно, из окружения или по умолчанию."""
    if repo:
        return repo
    import os
    return os.environ.get("SVYAZ_REPO") or GITHUB_REPO


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


def _releases_url(repo):
    """Готовый адрес запроса для конкретного репозитория."""
    return RELEASES_API.format(repo=_repo(repo))


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
      error      — текст ошибки, если ok=False.
    """
    current = current or APP_VERSION
    result = {
        "ok": False, "has_update": False, "current": current,
        "latest": "", "url": "", "notes": "", "error": "",
    }
    url = _releases_url(repo)
    try:
        if opener is not None:
            payload = opener(url, timeout)
        else:
            request = urllib.request.Request(
                url, headers={
                    "User-Agent": "SvyazCloud-Updater/" + APP_VERSION,
                    "Accept": "application/vnd.github+json",
                })
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8", "replace")
        data = json.loads(payload) if isinstance(payload, str) else payload
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            result["error"] = "На GitHub ещё нет ни одного выпуска"
        else:
            result["error"] = f"GitHub ответил ошибкой {exc.code}"
        return result
    except Exception as exc:  # noqa: BLE001 — сеть ненадёжна, гасим всё
        result["error"] = f"Не получилось связаться с GitHub: {exc}"
        return result

    if not isinstance(data, dict):
        result["error"] = "GitHub вернул непонятный ответ"
        return result
    latest = str(data.get("tag_name") or "").strip()
    if not latest:
        result["error"] = "GitHub не сообщил номер версии"
        return result
    result["ok"] = True
    result["latest"] = latest
    result["url"] = str(data.get("html_url") or RELEASES_PAGE.format(repo=_repo(repo)))
    body = data.get("body") or data.get("name") or ""
    result["notes"] = str(body).strip()
    result["has_update"] = has_update(current, latest)
    return result
