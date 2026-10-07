# СвязьКлауд — плеер с поиском по нескольким музыкальным площадкам.
#
# Поиск по названию идёт сразу на нескольких площадках, каждая отвечает своим
# потоком, а в результатах у трека указана платформа-источник:
#   * SoundCloud — поиск и скачивание через yt-dlp (префикс scsearch);
#   * YouTube     — поиск через yt-dlp (префикс ytsearch);
#   * Яндекс.Музыка — собственный разбор страницы поиска (см. search_yandex_music);
#   * ВК Музыка   — официальный API audio.search, нужен токен (вкладка настроек
#     «Площадки: токен ВК» или переменная SVYAZ_VK_TOKEN). Аудио из ВК качается
#     напрямую по ссылке на mp3, которую отдаёт сам API: yt-dlp ссылки
#     vk.com/audio не понимает, а обычный HTTP-запрос работает;
#   * Spotify     — открытый oEmbed по прямой ссылке на трек.

# Трек с любой из этих ссылок можно добавить в плейлист; ограничения площадок
# честно перечислены в PLATFORM_LIMITS.
#
# Текст песни распознаётся ЛОКАЛЬНО, без интернета, через OpenAI Whisper.
# Один раз установите зависимости в тот же Python, что запускает плеер:
#
#     pip install openai-whisper imageio-ffmpeg
#
# torch придёт сам как зависимость Whisper, а imageio-ffmpeg приносит ffmpeg,
# которым Whisper декодирует mp3 (если ffmpeg уже есть в PATH — пакет не нужен).
# Без них плеер работает как обычно, только текст песни не распознаётся.
# При первом распознавании модель один раз скачается (base ≈ 145 МБ), дальше — офлайн.
# Модель по умолчанию — "base": она заметно чище разбирает русскую речь, чем "tiny".
# Другой размер задаётся переменной окружения, например:  set SVYAZ_WHISPER_MODEL=small
# (small и medium распознают ещё точнее, но считают дольше).
# Язык по умолчанию Whisper определяет сам (автоопределение).
# Если для конкретных песен нужен один язык:  set SVYAZ_WHISPER_LANG=ru
# Язык проверки при сомнительном автоопределении: set SVYAZ_WHISPER_RECHECK=ru
import sys, os, re, math, json, shutil, hashlib, queue, threading, uuid, subprocess, pygame, struct, time
import urllib.request, urllib.parse
from PyQt6.QtCore import Qt, QUrl, QTimer, QSize, QThread, QObject, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QDialog,
                             QLineEdit, QPushButton, QLabel, QSlider, QColorDialog,
                             QListWidget, QListWidgetItem, QFileDialog, QGraphicsBlurEffect,
                             QSizePolicy, QMessageBox, QMenu, QScrollArea, QComboBox,
                             QInputDialog)
from PyQt6.QtGui import (QMovie, QPixmap, QFont, QFontMetrics, QPainter, QColor,
                         QIcon, QDesktopServices)

import lyrics_web  # поиск текста песни в интернете (Genius, lrclib, lyrics.ovh)

# --- Плейлист и поиск по музыкальным платформам -------------------------------
# Плейлист сохраняется в этот файл рядом с плеером и восстанавливается при запуске.
PLAYLIST_FILE = "playlist.json"
# Имя плейлиста по умолчанию — оно же у файла старого формата (до нескольких плейлистов).
DEFAULT_PLAYLIST_NAME = "Основной"
# Максимум символов в имени плейлиста — защита от «простыней» в комбобоксе.
MAX_PLAYLIST_NAME_LEN = 60


def normalize_playlist_name(name, fallback=DEFAULT_PLAYLIST_NAME):
    """Имя плейлиста для показа и хранения: пустое -> fallback, длинное обрежем."""
    cleaned = str(name or "").strip()
    if not cleaned:
        cleaned = str(fallback or DEFAULT_PLAYLIST_NAME).strip() or DEFAULT_PLAYLIST_NAME
    if len(cleaned) > MAX_PLAYLIST_NAME_LEN:
        cleaned = cleaned[:MAX_PLAYLIST_NAME_LEN].rstrip()
    return cleaned or DEFAULT_PLAYLIST_NAME


def unique_playlist_name(base, existing):
    """Свободное имя: «Микс», «Микс (2)», «Микс (3)»…"""
    base = normalize_playlist_name(base)
    known = existing or {}
    if base not in known:
        return base
    index = 2
    while f"{base} ({index})" in known:
        index += 1
    return f"{base} ({index})"


def load_playlist_store(path):
    """Все плейлисты из файла: (словарь имя -> треки, имя активного).

    Понимает новый формат {"playlists": {...}, "active": ...}, старый
    {"tracks": [...]} или plain-список, а также M3U/текст со ссылками.
    Битый или пустой файл даёт ({"Основной": []}, "Основной").
    """
    fallback = {DEFAULT_PLAYLIST_NAME: []}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return dict(fallback), DEFAULT_PLAYLIST_NAME
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    stripped = text.strip()
    if not stripped:
        return dict(fallback), DEFAULT_PLAYLIST_NAME
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            return dict(fallback), DEFAULT_PLAYLIST_NAME
        if isinstance(data, dict):
            playlists = data.get("playlists")
            if isinstance(playlists, dict) and playlists:
                store = {}
                for name, tracks in playlists.items():
                    clean = normalize_playlist_name(name)
                    if clean in store:
                        clean = unique_playlist_name(clean, store)
                    items = [t for t in (tracks or [])
                             if isinstance(t, dict) and (t.get("url") or t.get("path"))]
                    store[clean] = items
                if not store:
                    return dict(fallback), DEFAULT_PLAYLIST_NAME
                active = normalize_playlist_name(data.get("active"))
                if active not in store:
                    active = next(iter(store))
                return store, active
            if isinstance(data.get("tracks"), list):
                items = [t for t in data["tracks"]
                         if isinstance(t, dict) and (t.get("url") or t.get("path"))]
                return {DEFAULT_PLAYLIST_NAME: items}, DEFAULT_PLAYLIST_NAME
        elif isinstance(data, list):
            items = [t for t in data
                     if isinstance(t, dict) and (t.get("url") or t.get("path"))]
            return {DEFAULT_PLAYLIST_NAME: items}, DEFAULT_PLAYLIST_NAME
        return dict(fallback), DEFAULT_PLAYLIST_NAME
    tracks = parse_playlist_file(path)
    return {DEFAULT_PLAYLIST_NAME: tracks}, DEFAULT_PLAYLIST_NAME


def import_playlists_from_file(path):
    """Плейлисты из внешнего файла: словарь имя -> треки.

    Наш много-плейлистный JSON отдаёт все плейлисты сразу, остальные
    форматы — один плейлист с именем из названия файла.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return {}
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    stripped = text.strip()
    hint = ""
    if stripped and stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict):
            playlists = data.get("playlists")
            if isinstance(playlists, dict) and playlists:
                out = {}
                for name, tracks in playlists.items():
                    clean = normalize_playlist_name(name)
                    if clean in out:
                        clean = unique_playlist_name(clean, out)
                    items = [t for t in (tracks or [])
                             if isinstance(t, dict) and (t.get("url") or t.get("path"))]
                    if items:
                        out[clean] = items
                if out:
                    return out
            # Одиночный JSON с полем «name» — берём имя из него, а не из файла
            if data.get("name"):
                hint = normalize_playlist_name(data.get("name"))
    tracks = parse_playlist_file(path)
    if not tracks:
        return {}
    base = hint or os.path.splitext(os.path.basename(path or ""))[0].strip()
    return {normalize_playlist_name(base or DEFAULT_PLAYLIST_NAME): tracks}


def playlist_to_m3u(tracks):
    """Треки в текст M3U: #EXTINF с автором-названием и ссылка под ним."""
    lines = ["#EXTM3U"]
    for track in tracks or []:
        if not isinstance(track, dict):
            continue
        target = track.get("url") or track.get("path") or ""
        if not target:
            continue
        author = str(track.get("author") or "").strip()
        title = str(track.get("title") or target).strip()
        label = f"{author} - {title}" if author else title
        try:
            duration = int(float(track.get("duration") or 0) or 0)
        except (TypeError, ValueError):
            duration = 0
        lines.append(f"#EXTINF:{duration},{label}")
        lines.append(str(target))
    return "\n".join(lines) + "\n"
# Сколько лучших совпадений показывать при поиске с одной платформы (scsearch3 = три трека).
SEARCH_RESULTS = 3
# Файл кастомизации внешнего вида: цвета, шрифты, прозрачность, фоны, пресеты.
THEME_FILE = "theme_config.json"

# --- Платформы -----------------------------------------------------------------
# Проверено на живых запросах в этой версии yt-dlp (2026.08.19):
#   * SoundCloud — поиск scsearch и скачивание работают полностью;
#   * YouTube — префикс ytsearch есть, но www.youtube.com должен резолвиться в DNS;
#   * ВК — экстрактор «vk» берёт только видео (vk.com/audio не поддерживается);
#   * Яндекс.Музыка — экстрактор yandexmusic есть, но их веб-поиск открыт только браузеру;
#   * Spotify — треки защищены DRM, yt-dlp отказывает официально.
# Поэтому «can_search» отмечает только реально работающие префиксы поиска.
PLATFORMS = (
    {"id": "sc", "name": "SoundCloud", "prefix": "scsearch", "can_search": True,
     "hosts": ("soundcloud.com", "snd.sc")},
    {"id": "yt", "name": "YouTube", "prefix": "ytsearch", "can_search": True,
     "hosts": ("youtube.com", "youtu.be", "music.youtube.com")},
    {"id": "ym", "name": "Яндекс.Музыка", "prefix": "", "can_search": False,
     "hosts": ("music.yandex.ru", "music.yandex.com", "music.yandex.by",
               "music.yandex.kz", "music.yandex.ua")},
    {"id": "vk", "name": "VK", "prefix": "", "can_search": False,
     "hosts": ("vk.com", "vk.ru", "vkvideo.ru")},
    {"id": "spotify", "name": "Spotify", "prefix": "", "can_search": False,
     "hosts": ("open.spotify.com", "spotify.com", "play.spotify.com")},
)
PLATFORM_BY_ID = {p["id"]: p for p in PLATFORMS}
# Платформы, по которым поиск реально работает — их опрашиваем параллельно.
SEARCH_PLATFORMS = tuple(p["id"] for p in PLATFORMS if p["can_search"])
# Честные ограничения площадок. «Найти» и «скачать» — разные вещи: например,
# ВК не отдаёт аудио в yt-dlp, но мы качаем его напрямую по ссылке из VK API.
PLATFORM_LIMITS = {
    "spotify": "треки защищены DRM — найти можно, скачать нельзя",
}




def detect_platform(url):
    """Определяет платформу по ссылке. Возвращает id платформы или ""."""
    low = (url or "").strip().lower()
    if not low:
        return ""
    host = urllib.parse.urlparse(low if "//" in low else "https://" + low).netloc
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    for platform in PLATFORMS:
        for domain in platform["hosts"]:
            if host == domain or host.endswith("." + domain):
                return platform["id"]
    return ""


def platform_name(platform_id):
    """Человеческое имя платформы по её id (для подписи в результатах поиска)."""
    return PLATFORM_BY_ID.get(platform_id or "", {}).get("name", "")


def parse_playlist_file(path):
    """Треки из файла плейлиста: наш JSON, M3U/M3U8 или текст со ссылками.

    Понимает:
      * наш playlist.json старого формата (список или {"tracks": [...]})
        и нового (несколько плейлистов — берётся активный);
      * M3U/M3U8: строки #EXTINF:длительность,Исполнитель - Название
        и ссылки под ними;
      * простой текст, где на строку одна ссылка (остальные строки — мусор).

    Локальные файлы ({"path": ...}) тоже проходят. Пустой или битый файл
    даёт пустой список — без исключений.
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        tracks = None
        if isinstance(data, dict):
            playlists = data.get("playlists")
            if isinstance(playlists, dict) and playlists:
                # Наш файл нового формата: активный плейлист или первый
                active = str(data.get("active") or "").strip()
                chosen = (playlists.get(active) if active in playlists
                          else next(iter(playlists.values())))
                tracks = chosen if isinstance(chosen, list) else None
            elif isinstance(data.get("tracks"), list):
                tracks = data["tracks"]
        elif isinstance(data, list):
            tracks = data
        if tracks is None:
            return []
        return [t for t in tracks
                if isinstance(t, dict) and (t.get("url") or t.get("path"))]
    # M3U / текст
    tracks, pending = [], ""
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("#EXTINF:"):
            pending = line.split(",", 1)[1].strip() if "," in line else ""
            continue
        if line.startswith("#"):
            continue
        if not line.lower().startswith(("http://", "https://")):
            pending = ""              # не ссылка — и заголовок к ней не привязан
            continue
        platform = detect_platform(line) or "sc"
        title, author = pending, ""
        if " - " in pending:
            author, title = pending.split(" - ", 1)
        track = {"type": platform, "platform": platform, "url": line,
                 "title": title.strip()}
        if author.strip():
            track["author"] = author.strip()
        tracks.append(track)
        pending = ""
    return tracks


# --- Поиск на площадках, которых нет в yt-dlp -------------------------------
# Проверено на живых запросах 04.10.2026:
#   * Яндекс.Музыка — рабочий поиск: страница https://music.yandex.ru/search/?text=…
#     отдаёт результаты прямо в HTML, в блоках window.__STATE_PATCHES__. Оттуда
#     берём id, название, артистов, длительность, обложку и признак наличия
#     синхротекста. Официальный API без ключа отвечает 400, поэтому парсим страницу.
#   * ВК — поиск требует access_token (api.vk.com отвечает error 15), а поиск
#     на сайте отдаёт пустую страницу без входа. Поэтому работаем по ссылке,
#     если пользователь дал токен: https://api.vk.com/method/audio.search.
#   * Spotify — поиск отдаётся только после JavaScript, а oEmbed отвечает лишь
#     для уже известной ссылки на трек. Поэтому Spotify ищем через oEmbed по
#     готовой ссылке, а по названию — честно сообщаем, что нужен сам Spotify.
SEARCH_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
# Токен ВК для поиска по музыке. Пусто — поиск по ВК просто выключится.
VK_ACCESS_TOKEN = os.environ.get("SVYAZ_VK_TOKEN", "").strip()
# Сколько ждём молчащую площадку и как часто проверяем. Если площадка не ответила
# за SEARCH_TIMEOUT_SEC, показываем то, что успели найти остальные.
SEARCH_TIMEOUT_SEC = 45.0
SEARCH_POLL_MS = 250
# Платформы, которые опрашиваем при обычном поиске по названию. ВК включается
# только с токеном: без него его поиск всё равно закрыт, и опрашивать его впустую.
def default_search_platforms():
    """Список площадок для поиска: Яндекс.Музыка ищется всегда, ВК — с токеном."""
    wanted = list(SEARCH_PLATFORMS) + [p["id"] for p in PLATFORMS if not p["can_search"]]
    return tuple(p for p in dict.fromkeys(wanted)
                 if p != "vk" or VK_ACCESS_TOKEN)


SEARCH_ALL_PLATFORMS = default_search_platforms()


def active_search_platforms():
    """Площадки для поиска с учётом токена, введённого в настройках."""
    global VK_ACCESS_TOKEN
    token = load_vk_token() or VK_ACCESS_TOKEN
    if not token:
        return SEARCH_ALL_PLATFORMS
    wanted = list(SEARCH_ALL_PLATFORMS)
    if "vk" not in wanted:
        wanted.append("vk")
    return tuple(wanted)



def _ssl_context():
    """SSL с системными сертификатами и, если есть, с certifi.

    Нужен, потому что на части машин системные корни устарели, и Яндекс или
    Spotify начинают отвечать CERTIFICATE_VERIFY_FAILED.
    """
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()   # certifi нет — системные корни


def _vk_headers():
    """Заголовки для запросов к ВК.

    Токен, полученный через Kate Mobile, работает только с «родной» строкой
    User-Agent мобильного клиента: с обычным браузерным ВК ответит «нет
    доступа», хотя токен вполне рабочий. Поэтому UA хранится рядом с токеном.
    """
    headers = {"Accept": "application/json"}
    user_agent = load_vk_user_agent()
    if user_agent:
        headers["User-Agent"] = user_agent
    return headers


def _http_get(url, timeout=20, headers=None):
    """GET-запрос с браузерными заголовками. Возвращает текст или ""."""
    request = urllib.request.Request(
        url, headers={"User-Agent": SEARCH_USER_AGENT,
                      "Accept-Language": "ru,en;q=0.8",
                      **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout,
                                context=_ssl_context()) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, "replace")


def _balanced_objects(text, marker):
    """Достаёт из HTML все JSON-объекты, содержащие marker.

    Регулярное выражение здесь не годится: внутри объектов есть вложенные
    скобки и экранированные кавычки. Поэтому идём по тексту и балансируем скобки,
    уважая строковые литералы.
    """
    found = []
    for match in re.finditer(re.escape(marker), text):
        anchor = text.rfind('"value":{', 0, match.start())
        if anchor < 0:
            continue
        start = text.index("{", anchor + 8)
        depth, in_string, escaped = 0, False, False
        i = start
        while i < len(text):
            char = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    found.append(text[start:i + 1])
                    break
            i += 1
    return found

def search_yandex_music(query, limit=3):
    """Ищет треки в Яндекс.Музыке по названию. Возвращает список треков.

    Данные берём из HTML поиска: там лежит полная карточка — название, артисты,
    длительность, обложка и даже признак наличия синхротекста.
    """
    query = (query or "").strip()
    if not query:
        return []
    url = "https://music.yandex.ru/search/?text=" + urllib.parse.quote(query)
    try:
        html = _http_get(url, timeout=25)
    except Exception as exc:
        raise RuntimeError(f"Яндекс.Музыка недоступна: {type(exc).__name__}") from exc

    tracks, seen = [], set()
    # trackPosition есть у карточек результатов, best_result_track — у лучшего
    # результата, realId — в альбомах. Формат данных у них одинаковый.
    for marker in ('"trackPosition"', '"type":"best_result_track"', '"realId"'):
        for raw in _balanced_objects(html, marker):
            try:
                item = json.loads(raw)
            except ValueError:
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else item
            track_id = data.get("id")
            title = (data.get("title") or "").strip()
            if not track_id or not title or track_id in seen:
                continue
            seen.add(track_id)
            album_id = data.get("albumId") or 0
            artists = ", ".join(a.get("name", "") for a in data.get("artists", [])
                                if isinstance(a, dict) and a.get("name"))
            cover = str(data.get("coverUri") or "").replace("\\u002F", "/")
            cover_url = ("https://" + cover.replace("%%", "200x200")
                         if cover.startswith("avatars.yandex.net") else "")
            tracks.append({
                "type": "ym", "platform": "ym", "id": str(track_id),
                "title": title, "author": artists,
                "url": f"https://music.yandex.ru/album/{album_id}/track/{track_id}",
                "duration": round(int(data.get("durationMs") or 0) / 1000.0, 3),
                "cover": cover_url,
                "has_sync_lyrics": bool(data.get("hasSyncLyrics")),
            })
            if len(tracks) >= max(1, int(limit or 3)):
                return tracks
    return tracks


def search_vk_music(query, limit=3, token=None):
    """Ищет треки в ВК Музыке. Нужен access_token — иначе площадка не ищет.

    Токен берём из переменной окружения или файла настроек. Без него честно
    возвращаем пустой список: обходить авторизацию ВК мы не будем.

    В каждом найденном треке возвращаем direct_url — это прямая ссылка на mp3,
    которую отдаёт сам VK API. По ней файл качается обычным запросом, без
    yt-dlp, который ссылки vk.com/audio не понимает.
    """
    query = (query or "").strip()
    token = (token or VK_ACCESS_TOKEN or load_vk_token() or "").strip()
    if not query or not token:
        return []
    url = ("https://api.vk.com/method/audio.search?q="
           + urllib.parse.quote(query) + f"&count={max(1, int(limit or 3))}"
           + "&v=5.199" + f"&access_token={urllib.parse.quote(token)}")
    try:
        data = json.loads(_http_get(url, timeout=20,
                                   headers=_vk_headers()))
    except Exception:
        return []
    if "error" in data:
        raise RuntimeError(f"ВК: {data['error'].get('error_msg', 'ошибка поиска')}")
    items = (data.get("response") or {}).get("items") or []
    tracks = []
    for item in items[:max(1, int(limit or 3))]:
        owner, track_id = item.get("owner_id"), item.get("id")
        if not owner or not track_id:
            continue
        tracks.append({
            "type": "vk", "platform": "vk", "id": str(track_id),
            "title": item.get("title") or "",
            "author": item.get("artist") or "",
            "url": f"https://vk.com/audio{owner}_{track_id}",
            "duration": float(item.get("duration") or 0.0),
            "cover": item.get("thumb_100") or item.get("thumb_200") or "",
            # Прямая ссылка на сам mp3 — по ней качаем без yt-dlp
            "direct_url": item.get("url") or "",
        })
    return tracks



# --- Токен ВК: из настроек плеера или переменной окружения --------------------
# --- Ссылки ВК: разбор и получение трека по ссылке ------------------------------
def vk_audio_ids_from_url(url):
    """Достаёт (владелец, id) аудиозаписи из ссылки ВК любого вида.

    Подходят оба популярных формата:
      * https://vk.com/audio-123_456
      * https://vk.com/audio?z=audio-123_456%2F... (и wall-1_2?z=audio-...)
    Возвращает кортеж строк или None, если аудио в ссылке нет.
    """
    if not url:
        return None
    decoded = urllib.parse.unquote(str(url))
    match = re.search(r"audio(-?\d+)_(\d+)", decoded)
    if not match:
        return None
    return match.group(1), match.group(2)


def vk_audio_get_by_id(owner, audio_id, token=None):
    """Трек из ВК по его id: название, автор, обложка и прямая ссылка на mp3.

    Нужен тот же токен, что и для поиска (доступ к аудио). Без токена или
    при отказе ВК возвращаем пустой словарь — без исключений.
    """
    token = (token or VK_ACCESS_TOKEN or load_vk_token() or "").strip()
    if not token:
        return {}
    url = ("https://api.vk.com/method/audio.getById?audios="
           + urllib.parse.quote(f"{owner}_{audio_id}")
           + "&v=5.199" + f"&access_token={urllib.parse.quote(token)}")
    try:
        data = json.loads(_http_get(url, timeout=20, headers=_vk_headers()))
    except Exception:
        return {}
    if not isinstance(data, dict) or "error" in data:
        return {}
    items = data.get("response") or []
    if isinstance(items, dict):          # на старых версиях API — объект
        items = [items]
    item = items[0] if items else None
    if not isinstance(item, dict) or not item.get("url"):
        return {}
    return {
        "direct_url": item.get("url") or "",
        "title": item.get("title") or "",
        "author": item.get("artist") or "",
        "cover": item.get("thumb_100") or item.get("thumb_200") or "",
        "duration": float(item.get("duration") or 0.0),
    }


def vk_audio_from_link(url, token=None):
    """Трек по ссылке ВК (любого формата) через audio.getById."""
    ids = vk_audio_ids_from_url(url)
    if not ids:
        return {}
    return vk_audio_get_by_id(ids[0], ids[1], token=token)


def vk_token_path():
    """Файл с токеном ВК рядом с кэшем — чтобы настройка пережила перезапуск."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sc_cache")
    return os.path.join(folder, "vk_token.txt")


def load_vk_token():
    """Токен ВК: сначала файл настроек, затем переменная окружения."""
    try:
        with open(vk_token_path(), "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    return VK_ACCESS_TOKEN


def save_vk_token(token):
    """Сохраняет токен ВК в файл. Возвращает True, если записали."""
    token = (token or "").strip()
    try:
        os.makedirs(os.path.dirname(vk_token_path()), exist_ok=True)
        with open(vk_token_path(), "w", encoding="utf-8") as fh:
            fh.write(token)
        return True
    except OSError:
        return False


def vk_user_agent_path():
    """Файл с User-Agent, к которому привязан токен.

    Токен Kate Mobile работает только если запрос идёт с «родной» строкой
    User-Agent мобильного клиента. Сохраняем её рядом с токеном, иначе ВК
    начнёт отвечать «нет доступа» на рабочем токене.
    """
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sc_cache")
    return os.path.join(folder, "vk_user_agent.txt")


def load_vk_user_agent():
    """User-Agent для запросов к ВК: сохранённый или обычный браузерный."""
    try:
        with open(vk_user_agent_path(), "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    return ""


def save_vk_user_agent(user_agent):
    """Запоминаем User-Agent к токену. Пустая строка — возвращаемся к браузерной."""
    user_agent = (user_agent or "").strip()
    try:
        os.makedirs(os.path.dirname(vk_user_agent_path()), exist_ok=True)
        with open(vk_user_agent_path(), "w", encoding="utf-8") as fh:
            fh.write(user_agent)
        return True
    except OSError:
        return False


def spotify_track_info(url):
    """Метаданные трека Spotify по прямой ссылке через открытый oEmbed.

    Поиск по названию у Spotify требует авторизации, а вот для уже имеющейся
    ссылки oEmbed отдаёт название и исполнителя без токена.
    """
    match = re.search(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([0-9A-Za-z]{22})",
                      url or "")
    if not match:
        return {}
    api = ("https://open.spotify.com/oembed?url="
           + urllib.parse.quote(f"https://open.spotify.com/track/{match.group(1)}"))
    try:
        data = json.loads(_http_get(api, timeout=20))
    except Exception:
        return {}
    if not data.get("title"):
        return {}
    return {
        "type": "spotify", "platform": "spotify", "id": match.group(1),
        "title": data.get("title", ""), "author": data.get("author_name", ""),
        "url": f"https://open.spotify.com/track/{match.group(1)}",
        "duration": 0.0, "cover": data.get("thumbnail_url", ""),
    }


# --- Прямое скачивание аудио по ссылке на файл -------------------------------
# Зачем это нужно: ссылки вида vk.com/audio... не понимает yt-dlp (он берёт из
# ВК только видео). Но сам VK API отдаёт прямую ссылку на mp3, и вот её файл
# можно скачать обычным HTTP-запросом — без всяких обходов и лишних программ.
AUDIO_CONTENT_TYPES = ("audio/mpeg", "audio/mp3", "audio/mp4", "audio/x-m4a",
                       "audio/ogg", "audio/wav", "audio/aac", "audio/flac",
                       "application/octet-stream", "binary/octet-stream")


def guess_audio_ext(url, content_type=""):
    """Определяем расширение аудиофайла по ссылке или заголовку ответа."""
    path = urllib.parse.urlparse(url or "").path
    ext = os.path.splitext(path)[1].lower().strip()
    if ext in (".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav", ".flac"):
        return ".m4a" if ext == ".mp4" else ext
    content_type = (content_type or "").split(";")[0].strip().lower()
    if "mpeg" in content_type or "mp3" in content_type:
        return ".mp3"
    if "mp4" in content_type or "m4a" in content_type or "aac" in content_type:
        return ".m4a"
    if "ogg" in content_type or "opus" in content_type:
        return ".ogg"
    if "wav" in content_type:
        return ".wav"
    if "flac" in content_type:
        return ".flac"
    return ".mp3"          # ВК в основном отдаёт mp3


def download_audio(direct_url, target_base, timeout=45):
    """Скачивает аудиофайл по прямой ссылке. Возвращает путь или "".

    Ссылка ведёт на сам mp3, поэтому качаем потоком: треки бывают по 10 МБ.
    Если сервер не отдал аудио (например, отдал html-заглушку), честно
    возвращаем "" — вызывающий код тогда откроет трек в браузере.
    """
    if not direct_url or not str(direct_url).startswith("http"):
        return ""
    request = urllib.request.Request(direct_url, headers={
        "User-Agent": load_vk_user_agent() or SEARCH_USER_AGENT,
        "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
        "Referer": "https://vk.com/",
    })
    partial = target_base + ".part"
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=_ssl_context()) as response:
            content_type = response.headers.get("Content-Type", "")
            total = 0
            with open(partial, "wb") as handle:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    handle.write(chunk)
                    if total > 80 * 1024 * 1024:      # больше 80 МБ — не трек
                        raise OSError("файл подозрительно большой")
    except Exception as exc:
        console(f"Прямое скачивание не удалось: {type(exc).__name__}: {exc}")
        try:
            os.remove(partial)
        except OSError:
            pass
        return ""
    # Слишком короткий ответ — это не аудио, а заглушка или страница ошибки
    if total < 20000:
        try:
            os.remove(partial)
        except OSError:
            pass
        console(f"Прямое скачивание вернуло всего {total} байт — это не аудио")
        return ""
    ext = guess_audio_ext(direct_url, content_type)
    target = os.path.splitext(target_base)[0] + ext
    try:
        os.replace(partial, target)
    except OSError:
        target = partial
    console(f"Аудио скачано напрямую: {os.path.basename(target)} ({total // 1024} КБ)")
    return target






# --- Темы и профили кастомизации -----------------------------------------------
# Кнопки, цвет каждой из которых настраивается отдельно в окне настроек.
THEMEABLE_BUTTONS = (
    ("btn_add_playlist", "Плейлист"),
    ("btn_play_now", "Запустить сразу"),
    ("btn_local", "С ПК"),
    ("btn_search", "Поиск"),
    ("btn_remove_track", "Удалить из плейлиста"),
    ("btn_save_list", "Сохранить список"),
    ("btn_playlist_new", "Новый плейлист"),
    ("btn_playlist_import", "Импорт плейлиста"),
    ("btn_playlist_export", "Экспорт плейлиста"),
    ("btn_playlist_delete", "Удалить плейлист"),
    ("btn_playlist_merge", "Всё в текущий"),
    ("btn_prev", "Назад"),
    ("btn_pause", "Пауза"),
    ("btn_next", "Вперёд"),
    ("btn_repeat", "Повтор"),
    ("btn_settings", "Шестерёнка"),
)
THEME_LABELS = dict(THEMEABLE_BUTTONS)
THEME_BUTTON_IDS = tuple(key for key, _label in THEMEABLE_BUTTONS)
DEFAULT_THEME = {
    "accent": "#8a2be2",
    "opacity": 0.9,
    "font": "Segoe UI",
    "font_index": 0,
    "background": "",
    "blur": False,
    "volume": 0.7,
    "lyrics_color": "#8a2be2",
    "buttons": {},
}


def theme_path():
    """Файл кастомизации рядом с плеером — переживает перезапуск."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), THEME_FILE)


def _color_name(value, fallback="#8a2be2"):
    """Приводит цвет к строке #RRGGBB; битое значение заменяем запасным."""
    color = QColor(value) if isinstance(value, str) else QColor()
    return color.name() if color.isValid() else fallback


def load_theme_config():
    """Читает theme_config.json. Любая ошибка — просто тема по умолчанию."""
    data = dict(DEFAULT_THEME)
    data["buttons"] = {}
    try:
        with open(theme_path(), "r", encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return data
    if not isinstance(stored, dict):
        return data
    presets = stored.get("presets")
    last = stored.get("current")
    if isinstance(presets, dict) and isinstance(last, str) and last in presets:
        chosen = presets[last]
    else:
        chosen = stored
    if not isinstance(chosen, dict):
        return data
    data["accent"] = _color_name(chosen.get("accent"), DEFAULT_THEME["accent"])
    data["lyrics_color"] = _color_name(chosen.get("lyrics_color"),
                                       chosen.get("accent", DEFAULT_THEME["lyrics_color"]))
    try:
        data["opacity"] = max(0.2, min(1.0, float(chosen.get("opacity", 0.9))))
    except (TypeError, ValueError):
        pass
    try:
        data["volume"] = max(0.0, min(1.0, float(chosen.get("volume", 0.7))))
    except (TypeError, ValueError):
        pass
    data["font"] = str(chosen.get("font") or DEFAULT_THEME["font"])
    try:
        data["font_index"] = int(chosen.get("font_index", 0))
    except (TypeError, ValueError):
        data["font_index"] = 0
    data["background"] = str(chosen.get("background") or "")
    data["blur"] = bool(chosen.get("blur"))
    buttons = chosen.get("buttons")
    if isinstance(buttons, dict):
        data["buttons"] = {str(k): _color_name(v, data["accent"])
                           for k, v in buttons.items()}
    return data




def _decode_ytdlp(data):
    """Декодируем вывод yt-dlp, не теряя русские буквы.

    yt-dlp на Windows печатает в кодировке консоли (обычно cp1251), поэтому
    декодировать «как UTF-8» нельзя: русские буквы превращаются в «�» без
    возможности восстановления. Поэтому пробуем сначала UTF-8, затем кодировку
    консоли Windows.
    """
    if isinstance(data, str):
        return data
    if not data:
        return ""
    for encoding in ("utf-8", "cp1251", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "�" not in text:            # не оставляем «кракозябр» ни в одном варианте
            return text
    for encoding in ("utf-8", "cp1251", "cp1252"):
        return data.decode(encoding, "replace")
    return ""


def _ytdlp_env():
    """Окружение для yt-dlp: заставляем печатать UTF-8, чтобы не гадать о кодировке."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env

# --- Локальное распознавание текста песни (Whisper) --------------------------
# Модель по умолчанию — "small". Замерено на реальном треке:
#   base  -> logprob -1.40, текст: «все твои мы сли ничем, простанно родить горачу»
#   small -> logprob -0.38, текст: «все твои мысли ни о чем, постоянно грустишь горько»
# small читает песни в ~3.7 раза увереннее, время растёт примерно вдвое.
# Ещё точнее:  set SVYAZ_WHISPER_MODEL=medium   (медленно, нужно ~5 ГБ RAM)
WHISPER_MODEL_NAME = os.environ.get("SVYAZ_WHISPER_MODEL", "small")
WHISPER_PIP_PACKAGES = ("openai-whisper", "imageio-ffmpeg")
# Язык по умолчанию определяется автоматически (None — не подсказываем Whisper язык).
# Если нужен конкретный:  set SVYAZ_WHISPER_LANG=ru  (для украинского: uk, и т.д.)
_whisper_lang = os.environ.get("SVYAZ_WHISPER_LANG", "auto").strip().lower()
WHISPER_LANGUAGE = None if _whisper_lang in ("", "auto", "none") else _whisper_lang
# Языки, которые Whisper часто путает с русским на музыке. Если он выбрал
# одного из них, делаем вторую попытку с языком по умолчанию и сравниваем
# качество: так текст читается правильно, а не «уезжает» в чужую орфографию.
WHISPER_RECHECK_LANGS = ("uk", "be", "bg", "pl", "sr", "hr", "bs", "mk")
WHISPER_FALLBACK_LANG = os.environ.get("SVYAZ_WHISPER_RECHECK", "ru").strip().lower() or "ru"
# Разделение вокала от музыки (demucs). Проверено на реальном треке: результат
# ХУЖЕ (logprob -1.40 -> -3.60), потому что Demucs вырезает вместе с вокалом
# часть звука, а Whisper такие огрызки читает ещё хуже. Поэтому по умолчанию
# выключено. Включить для эксперимента:  set SVYAZ_WHISPER_VOCALS=1
WHISPER_USE_VOCALS = os.environ.get("SVYAZ_WHISPER_VOCALS", "0").strip() in ("1", "on", "yes")
WHISPER_INSTALL_HINT = ("Текст песни недоступен: в этом Python нет Whisper.\n"
                        "Запустите «Запустить плеер.cmd» — он выберет нужный Python.\n"
                        "Или установите вручную:\n"
                        f'"{sys.executable}" -m pip install openai-whisper imageio-ffmpeg')
WHISPER_FFMPEG_HINT = ("Текст песни не распознан: Whisper нужен ffmpeg для чтения mp3 — "
                       "выполните: pip install imageio-ffmpeg")


def console(text):
    """Печатает в консоль, не роняя приложение на символах вне кодовой страницы Windows."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


# --- Синхронный текст песни (LRC) ----------------------------------------------
# Формат строки LRC: [минуты:секунды.сотые] текст строчки, например [01:23.45] Привет
LRC_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# Метки, которые не являются временем: [ti:…], [ar:…], [offset:…] и подобные.
LRC_META_RE = re.compile(r"^\[[a-zA-Z]+:")


def parse_lrc(text):
    """Разбирает LRC-текст в список (секунды, строка), отсортированный по времени.

    Понимает и несколько меток времени в одной строке ([00:10.00][01:20.00] текст),
    и вариант с точками вместо двоеточия. Строки без меток и служебные теги
    ([ti:…], [ar:…]) пропускаются — в караоке они не нужны.
    """
    parsed = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or LRC_META_RE.match(line):
            continue
        stamps = list(LRC_TIME_RE.finditer(line))
        if not stamps:
            continue
        # Убираем все метки времени — остаётся чистый текст строчки
        body = LRC_TIME_RE.sub("", line).strip()
        if not body:
            continue
        for stamp in stamps:
            minutes, seconds, fraction = stamp.groups()
            # Дробная часть бывает и как .45, и как .450 — приводим к секундам
            frac = float("0." + fraction) if fraction else 0.0
            parsed.append((int(minutes) * 60.0 + int(seconds) + frac, body))
    parsed.sort(key=lambda pair: pair[0])
    return parsed


def active_lrc_index(lines, seconds):
    """Индекс строчки, которая звучит на позиции seconds (или -1, если тишина)."""
    index = -1
    for i, (stamp, _body) in enumerate(lines):
        if stamp <= seconds:
            index = i
        else:
            break
    return index


def segments_to_lrc(segments):
    """Собирает LRC-текст из сегментов Whisper: у них уже есть точные таймкоды.

    Именно поэтому караоке работает «по ритму»: модель отдаёт время начала
    каждой фразы, а мы превращаем это в готовый файл <трек>.lrc.
    """
    lines = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text", "")).strip()
        start = seg.get("start")
        if not text or start is None:
            continue
        total = int(round(float(start) * 100))
        stamp = f"[{total // 6000:02d}:{(total % 6000) // 100:02d}.{total % 100:02d}]"
        lines.append(stamp + " " + text)
    return "\n".join(lines)


class WaveformWidget(QWidget):
    """Вейвформа звуковой шкалы: реальный анализ амплитуды трека + живая анимация."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(70)
        self.bar_count = 40
        self.phase = 0
        self.accent_color = "#8a2be2"
        self._real_data = []
        self._progress = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(80)

    def set_accent(self, color):
        self.accent_color = color
        self.update()

    def set_real_data(self, amplitudes):
        """Устанавливает реальные амплитуды трека, вычисленные при анализе файла."""
        self._real_data = amplitudes or []
        self.update()

    def set_progress(self, ratio):
        """Прогресс воспроизведения (0.0 .. 1.0) для подсветки проигранной части."""
        self._progress = max(0.0, min(1.0, ratio))
        self.update()

    def _animate(self):
        self.phase = (self.phase + 1) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return
        n = self.bar_count
        gap = 3
        bar_w = max(2.0, (w - gap * (n - 1)) / n)
        base_color = QColor(self.accent_color)
        painter.setPen(Qt.PenStyle.NoPen)
        has_real = bool(self._real_data and len(self._real_data) == n)
        for i in range(n):
            if has_real:
                raw_amp = self._real_data[i]
                dyn = 0.85 + 0.15 * math.sin(math.radians(self.phase * 3 + i * 17))
                amp = max(0.08, min(1.0, raw_amp * dyn))
            else:
                amp = 0.25 + 0.75 * abs(math.sin(math.radians(self.phase * 3 + i * 17)))
            bar_h = max(6.0, amp * (h - 10))
            x = i * (bar_w + gap)
            y = (h - bar_h) / 2
            color = QColor(base_color)
            is_past = (i / max(1, n - 1)) <= self._progress
            alpha = 220 + int(amp * 35) if is_past else 90 + int(amp * 70)
            color.setAlpha(min(255, alpha))
            painter.setBrush(color)
            painter.drawRoundedRect(int(x), int(y), int(bar_w), int(bar_h), 2, 2)
        painter.end()


class LyricsMarquee(QWidget):
    """Текст песни (караоке/lyrics): блок из нескольких строк с бегущей прокруткой."""

    def __init__(self, visible_lines=4, parent=None):
        super().__init__(parent)
        self.visible_lines = visible_lines
        self._raw_text = ""
        self._all_lines = []
        self._start = 0            # индекс верхней строки, которая «уезжает» наверх
        self._offset = 0.0         # сдвиг внутри строки, px (растёт -> текст ползёт вверх)
        self._intro = 0.0          # 1..0 — вступительное «выплывание» текста снизу
        self._tick = 0
        self._sep = "   •   "
        self._pad = 8
        self._fade = 0.75          # доля высоты строки, отведённая на «таяние» у краёв
        self._line_sec = 4.5       # секунд на то, чтобы строка прошла снизу вверх
        self._intro_sec = 1.4      # секунд на вступительное выплывание
        self._last_width = 0
        self._text_color = QColor("#8a2be2")
        self._shadow_color = QColor(0, 0, 0, 170)
        self._font = QFont("Segoe UI", 12)
        self._font.setItalic(True)
        self._line_h = max(1, QFontMetrics(self._font).height() + 4)
        self.setMinimumHeight(self._total_height())
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(33)      # ~30 кадров в секунду: движение выглядит плавным
        # Режим караоке: строчки с таймкодами LRC, активная подсвечивается
        self._lrc = []             # список (секунды, строка)
        self._lrc_index = -1       # какая строчка звучит сейчас
        self._lrc_scroll = 0.0     # плавный ход блока к активной строке, px
        self._lrc_map = []         # номер строки караоке -> строка после переноса
        self._lrc_highlight = QColor("#ffffff")

    def set_theme(self, text, font_family):
        """Цвет текста и шрифт берём из общей кастомизации плеера."""
        self._text_color = QColor(*text) if isinstance(text, (tuple, list)) else QColor(text)
        self._font = QFont(font_family, 12)
        self._font.setItalic(True)
        self._line_h = max(1, QFontMetrics(self._font).height() + 4)
        self.setMinimumHeight(self._total_height())
        self._rewrap()
        self.update()

    def set_scroll_text(self, text):
        """Новый текст всегда выплывает снизу, а не появляется рывком."""
        self._raw_text = text or ""
        self._start = 0
        self._offset = 0.0
        self._intro = 1.0 if self._raw_text else 0.0
        self._tick = 0
        self._lrc = []              # обычный текст — режим караоке выключается
        self._lrc_index = -1
        self._rewrap()
        self.update()

    # --- Караоке: текст идёт строго по ритму песни ---------------------------
    def set_lrc(self, text):
        """Включает режим караоке по LRC-тексту (таймкоды вида [01:23.45]).

        Если таймкодов нет, ведём себя как обычный блок текста — тогда старый
        плавный ход остаётся в силе и ничего не ломается.
        """
        parsed = parse_lrc(text)
        if not parsed:
            self._lrc = []
            self._lrc_index = -1
            self.set_scroll_text(" ".join(body for _stamp, body in parsed)
                                 or (text or ""))
            return False
        self._lrc = parsed
        self._lrc_index = -1
        self._lrc_scroll = 0.0
        # Подставляем строки караоке в общий перенос строк: длинные реплики
        # точно так же переносятся по словам, как в обычном режиме.
        self._raw_text = "\n".join(body for _stamp, body in parsed)
        self._start = 0
        self._offset = 0.0
        self._intro = 0.0          # в караоке текст не выплывает — он появляется по таймкодам
        self._tick = 0
        self._rewrap(keep_newlines=True)
        self.update()
        return True

    def has_lrc(self):
        """Есть ли синхронные таймкоды (тогда текст двигается по ритму)."""
        return bool(self._lrc)

    def set_position(self, seconds):
        """Подсвечивает и подкручивает блок к строке, звучащей на позиции seconds."""
        if not self._lrc:
            return -1
        index = active_lrc_index(self._lrc, max(0.0, float(seconds or 0.0)))
        if index != self._lrc_index:
            self._lrc_index = index
            self.update()
        return index

    def _lrc_top_line(self):
        """Индекс первой видимой строки караоке: активная всегда в центре блока."""
        if self._lrc_index < 0:
            return 0
        target = self._lrc_map[self._lrc_index] if self._lrc_index < len(self._lrc_map) else 0
        return max(0, target - self.visible_lines // 2)

    def _rewrap(self, keep_newlines=False):
        fm = QFontMetrics(self._font)
        avail = max(60, self.width() - 2 * self._pad)
        lines = []
        # В караоке каждая реплика LRC — отдельная строка, перенос только по словам
        chunks = self._raw_text.split("\n") if keep_newlines else [self._raw_text]
        if keep_newlines:
            # Запоминаем, на какой строке переноса начинается каждая реплика:
            # длинная реплика занимает две строки, и подсветка должна знать обе.
            self._lrc_map = []
        for chunk in chunks:
            if keep_newlines:
                self._lrc_map.append(len(lines))
            cur = ""
            for w in chunk.split():
                cand = (cur + " " + w).strip()
                if not cur or fm.horizontalAdvance(cand) <= avail:
                    cur = cand
                else:
                    lines.append(cur)
                    cur = w
            lines.append(cur)
        self._all_lines = lines
        if self._start >= len(lines):
            self._start = 0

    def _animate(self):
        """Кадр анимации: сначала текст выплывает снизу, потом плавно ползёт вверх."""
        if self._lrc:
            self._animate_karaoke()
            return
        if not self._all_lines:
            return                      # текст не задан — не тратим кадры впустую
        dt = self._timer.interval() / 1000.0
        if self._intro > 0.0:
            self._intro = max(0.0, self._intro - dt / self._intro_sec)
        elif len(self._all_lines) > 1:
            self._offset += self._line_h * dt / self._line_sec
            while self._offset >= self._line_h:      # строка целиком ушла наверх
                self._offset -= self._line_h
                self._start = (self._start + 1) % len(self._all_lines)
        self._tick += 1
        self.update()

    def _animate_karaoke(self):
        """В караоке позиция задаётся музыкой, а не таймером.

        Блок плавно доезжает к активной строке, а дальше стоит на месте до
        следующей реплики — текст идёт ровно по ритму песни.
        """
        self._tick += 1
        target = self._lrc_top_line()
        delta = target - self._start
        if delta:
            # Догоняем нужную строку быстро, но без рывка: ~0.18 с на строку
            self._start += delta * 0.35
            if abs(target - self._start) < 0.02:
                self._start = float(target)
        self.update()

    # --- Геометрия плавного хода (нужна и тестам) ----------------------------
    def _margin(self):
        """Полоса сверху и снизу, в которой строки проявляются и растворяются."""
        return max(6, int(self._line_h * self._fade))

    def _fade_band(self):
        """Полоса таяния сверху и снизу: margin + половина строки.

        Именно столько нужно, чтобы строка успела погаснуть ДО края виджета:
        при чистом margin верхняя строка обрезалась краем окна (alpha ~0.35).
        """
        return self._margin() + self._line_h / 2.0

    def _area_top(self):
        return max(self._fade_band(),
                   (self.height() - self.visible_lines * self._line_h) / 2.0)

    def _area_bottom(self):
        return self._area_top() + self.visible_lines * self._line_h

    def _total_height(self):
        # Блок + две полосы таяния: так текст гаснет внутри виджета, а не на его краю
        return int(round(self.visible_lines * self._line_h + 2 * self._fade_band()))

    def _scroll_offset(self):
        """Полный сдвиг отрисовки: выплывание снизу плюс рабочий ход вверх."""
        if self._lrc:
            # В караоке блок стоит на месте активной строки, без «выплывания»
            return -self._intro * self.visible_lines * self._line_h
        return self._offset - self._intro * self.visible_lines * self._line_h

    def _alpha_for(self, y):
        """Прозрачность строки по её верхнему краю: 1 в центре блока, 0 за краями.

        Зона таяния идёт от края виджета до середины самой строки: строка гаснет
        ровно на границе окна (без резкого обреза) и внутри блока читается полностью.
        """
        half = self._line_h / 2.0
        reach = self._margin() + half
        center_y = y + half
        a = min((center_y - (self._area_top() - self._margin())) / reach,
                ((self._area_bottom() + self._margin()) - center_y) / reach, 1.0)
        if a <= 0.0:
            return 0.0
        return a * a * (3 - 2 * a)      # плавные края вместо резкой границы

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.width() != self._last_width:
            self._last_width = self.width()
            self._rewrap()

    def _fit_line(self, line, fm, avail, index):
        """Длинная строка бежит по горизонтали, короткая показывается целиком.

        Смещение считаем в пикселях по всей длине строки: так бегунок идёт
        плавно, без ступенек по целому знаку, и не «зависает» на коротком тексте.
        """
        if fm.horizontalAdvance(line) <= avail:
            return line
        total = fm.horizontalAdvance(line)
        speed = self._line_h / 8.0                # медленно, чтобы успевали читать
        offset = (self._tick * speed + index * 37.0) % total
        out, x, width = [], 0.0, 0.0
        for ch in line:
            w = fm.horizontalAdvance(ch)
            if offset < x + w and width + w <= avail:   # строго вписываемся в ширину
                out.append(ch)
                width += w
            x += w
            if width >= avail:
                break
        return "".join(out) or line[:1]

    def paintEvent(self, event):
        # Фона и рамки нет — текст рисуется прямо на фоне окна
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font)
        fm = QFontMetrics(self._font)
        avail = max(20, self.width() - 2 * self._pad)
        painter.setClipRect(self._pad, 0, avail, self.height())
        count = len(self._all_lines)
        if not count:
            painter.end()
            return
        offset = self._scroll_offset()
        # +2 строки: одна уже наполовину уплыла наверх, вторая ещё выплывает снизу
        for k in range(self.visible_lines + 2):
            y = self._area_top() + k * self._line_h - offset
            if y > self.height():
                break
            alpha = self._alpha_for(y)
            if alpha <= 0.02:
                continue
            row = self._start + k
            shown = self._fit_line(self._all_lines[row % count], fm, avail, k)
            baseline = int(y + fm.ascent())
            active = self._lrc and self._lrc_row_active(row)
            if active:
                # Поющая строка: белая, жирная, с подложкой — её видно сразу
                font = QFont(self._font)
                font.setBold(True)
                font.setItalic(False)
                painter.setFont(font)
                fm_active = QFontMetrics(font)
                if fm_active.horizontalAdvance(shown) > avail:
                    shown = fm_active.elidedText(shown, Qt.TextElideMode.ElideRight, avail)
                bg = QColor(0, 0, 0, 90)
                width = fm_active.horizontalAdvance(shown)
                painter.fillRect(self._pad - 3, int(y), width + 6,
                                max(1, int(self._line_h - 2)), bg)
                color = QColor(self._lrc_highlight)
                painter.setPen(color)
                painter.drawText(self._pad, baseline, shown)
                painter.setFont(self._font)
                continue
            # лёгкая тень для читаемости поверх фото/GIF
            shadow = QColor(self._shadow_color)
            shadow.setAlphaF(shadow.alphaF() * alpha)
            painter.setPen(shadow)
            painter.drawText(self._pad + 1, baseline + 1, shown)
            color = QColor(self._text_color)
            color.setAlphaF(min(1.0, alpha))
            painter.setPen(color)
            painter.drawText(self._pad, baseline, shown)
        painter.end()

    def _lrc_row_active(self, row):
        """Относится ли строка переноса к реплике, которая сейчас звучит.

        Возвращаем ровно диапазон строк активной реплики: идущие следом строки
        того же переноса тоже подсвечены, а всё, что было до неё, уже не выделено
        (иначе при смене реплики старая строка оставалась бы горящей).
        """
        if not self._lrc_map or self._lrc_index < 0:
            return False
        if self._lrc_index >= len(self._lrc_map):
            return False
        start = self._lrc_map[self._lrc_index]
        end = (self._lrc_map[self._lrc_index + 1]
               if self._lrc_index + 1 < len(self._lrc_map)
               else len(self._all_lines))
        return start <= row < end

class LyricsWebWorker(QThread):
    """Ищет текст песни в интернете. Не нашли — сигнал not_found, тогда зовём Whisper."""

    found = pyqtSignal(str, str, str, str)   # название, путь аудио, текст, источник
    not_found = pyqtSignal(str, str)         # название, путь аудио
    failed = pyqtSignal(str, str)

    _alive = []

    def __init__(self, audio_path, track_title, artist="", parent=None):
        super().__init__(parent)
        self._audio_path = audio_path or ""
        self._track_title = track_title
        self._artist = artist or ""
        LyricsWebWorker._alive.append(self)
        self.finished.connect(self._forget)

    def _forget(self):
        try:
            LyricsWebWorker._alive.remove(self)
        except ValueError:
            pass

    def run(self):
        try:
            import lyrics_web
        except Exception as exc:
            self.not_found.emit(self._track_title, self._audio_path)
            console(f"Поиск текста выключен: {exc}")
            return
        if not lyrics_web.is_enabled():
            self.not_found.emit(self._track_title, self._audio_path)
            return
        try:
            text, source = lyrics_web.find(self._track_title, self._artist)
        except Exception as exc:           # сеть упала — не молчим, а идём в Whisper
            console(f"Поиск текста не удался: {type(exc).__name__}: {exc}")
            self.not_found.emit(self._track_title, self._audio_path)
            return
        if self.isInterruptionRequested():
            return
        if text:
            self.found.emit(self._track_title, self._audio_path, text, source)
        else:
            self.not_found.emit(self._track_title, self._audio_path)


class WhisperWorker(QThread):
    """Локальное распознавание текста песни (OpenAI Whisper) — интернет не нужен.

    Результат отдаётся сигналом transcribed(название, путь, текст), проблемы —
    failed(название, причина), missing_dependency(название) и missing_ffmpeg(название).
    Язык определяется Whisper автоматически. Если модель ошиблась и выбрала
    «родственника» (украинский, белорусский, польский…), делаем вторую попытку
    с языком по умолчанию и берём тот вариант, в который модель верит сильнее.
    """

    transcribed = pyqtSignal(str, str, str)   # название трека, путь к аудио, текст
    lrc = pyqtSignal(str, str, str)        # название трека, путь к аудио, LRC-таймкоды
    failed = pyqtSignal(str, str)
    missing_dependency = pyqtSignal(str, str)   # название трека, причина сбоя импорта
    missing_ffmpeg = pyqtSignal(str)
    _note = pyqtSignal(str)         # этапы подготовки: разделение вокала и т.п.

    _model_cache = {}          # модель грузится один раз на весь запуск плеера
    _run_lock = threading.Lock()   # одновременно распознаём только один трек
    _alive = []                # держим ссылки, чтобы QThread не удалялся на ходу

    def __init__(self, audio_path, track_title, model_name=WHISPER_MODEL_NAME,
                 language=WHISPER_LANGUAGE, parent=None):
        super().__init__(parent)
        self._audio_path = audio_path
        self._track_title = track_title
        self._model_name = model_name
        self._language = language
        WhisperWorker._alive.append(self)
        self.finished.connect(self._forget)

    def _forget(self):
        try:
            WhisperWorker._alive.remove(self)
        except ValueError:
            pass

    @staticmethod
    def _ensure_ffmpeg():
        """Whisper читает mp3 через ffmpeg: нет в PATH — берём сборку из imageio-ffmpeg."""
        if shutil.which("ffmpeg"):
            return True
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return False
        if not exe or not os.path.exists(exe):
            return False
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sc_cache", "ffmpeg")
        target = os.path.join(folder, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        try:
            os.makedirs(folder, exist_ok=True)
            if not os.path.exists(target) or os.path.getsize(target) != os.path.getsize(exe):
                shutil.copyfile(exe, target)   # один раз кладём рядом с кэшем плеера
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        except OSError:
            return False
        return bool(shutil.which("ffmpeg"))

    def _load_model(self):
        """Держим модель в памяти: второй трек распознаётся без повторной загрузки."""
        import whisper
        model = WhisperWorker._model_cache.get(self._model_name)
        if model is None:
            model = whisper.load_model(self._model_name)
            WhisperWorker._model_cache[self._model_name] = model
        return model

    def _decode_options(self, language=None):
        """Параметры распознавания. language=None — Whisper определяет язык сам.

        В режиме автоопределения не даём никаких подсказок: иначе подсказка сама
        навязывает орфографию и «ломает» определение языка.
        """
        options = {
            "fp16": False,                        # считаем в полной точности (CPU)
            "condition_on_previous_text": False,  # иначе на музыке фразы зацикливаются
        }
        if language:
            options["language"] = language
            options["initial_prompt"] = f"Текст песни на языке {language}."
        return options

    def _needs_recheck(self, result):
        """Перепроверяем ли язык: родственники, которые на музыке путают с русским."""
        if self._language:                       # язык задан пользователем — не спорим
            return False
        detected = str(result.get("language") or "").lower()
        return detected in WHISPER_RECHECK_LANGS

    @staticmethod
    def _confidence(result):
        """Насколько уверенно модель: средний log-вероят, ближе к нулю — лучше."""
        segments = result.get("segments") or []
        if segments:
            values = [float(s.get("avg_logprob", -9.0)) for s in segments
                      if s.get("avg_logprob") is not None]
            if values:
                return sum(values) / len(values)
        value = result.get("avg_logprob")
        return float(value) if value is not None else -9.0

    def _source_for_speech(self):
        """Файл для распознавания: чистый вокал, если Demucs его отделил.

        Whisper обучен на чистой речи. В песне вокал поёт поверх музыки, и без
        разделения модель слышит кашу. Один раз отделяем вокал и кэшируем его.
        """
        if not WHISPER_USE_VOCALS:
            return self._audio_path
        if self.isInterruptionRequested():
            return self._audio_path
        try:
            import vocals
        except Exception:
            return self._audio_path
        return vocals.prepare_vocals(self._audio_path,
                                     progress=self._note)

    def _transcribe_best(self, model, audio_path=None):
        """Распознаём и при необходимости перепроверяем язык — берём лучший вариант."""
        audio_path = audio_path or self._audio_path
        result = model.transcribe(audio_path, **self._decode_options(self._language))
        detected = str(result.get("language") or "?")
        if not self._needs_recheck(result):
            console(f"Whisper: язык трека — {detected}")
            return result
        alt = model.transcribe(
            audio_path, **self._decode_options(WHISPER_FALLBACK_LANG))
        alt_score, auto_score = self._confidence(alt), self._confidence(result)
        # Сравниваем среднюю уверенность: побеждает вариант, в который модель
        # «верит» сильнее, поэтому украинская песня останется украинской.
        if alt_score > auto_score:
            console(f"Whisper: язык определён как {detected}, но {WHISPER_FALLBACK_LANG} "
                    f"увереннее ({alt_score:.2f} > {auto_score:.2f}) — берём его")
            return alt
        console(f"Whisper: язык {detected} подтверждён ({alt_score:.2f} <= {auto_score:.2f})")
        return result

    def run(self):
        if not self._audio_path or not os.path.exists(self._audio_path):
            self.failed.emit(self._track_title, "файл трека не найден")
            return
        try:
            import whisper  # noqa: F401 — только проверка наличия зависимости
        except Exception as exc:
            # Причину показываем честно: «не установлен» — это часто совсем не так,
            # бывает сломанный torch, чужой интерпретатор или битая сборка numba.
            self.missing_dependency.emit(self._track_title, f"{type(exc).__name__}: {exc}")
            return
        if not self._ensure_ffmpeg():
            self.missing_ffmpeg.emit(self._track_title)
            return
        # Замок даёт точку отмены: если трек уже сменили, распознавание не начнём
        with WhisperWorker._run_lock:
            if self.isInterruptionRequested():
                return
            try:
                model = self._load_model()
                if self.isInterruptionRequested():
                    return
                audio_path = self._source_for_speech()
                if self.isInterruptionRequested():
                    return
                result = self._transcribe_best(model, audio_path)
            except Exception as exc:
                self.failed.emit(self._track_title, str(exc))
                return
            if self.isInterruptionRequested():
                return
            text = self._clean_text(result.get("text", ""))
            # Отдаём и обычный текст, и таймкоды: у сегментов Whisper есть точное
            # время начала каждой фразы — из них получается караоке «по ритму».
            lrc_text = segments_to_lrc(result.get("segments"))
        if text and self._looks_like_speech(text):
            self.transcribed.emit(self._track_title, self._audio_path, text)
            if lrc_text:
                self.lrc.emit(self._track_title, self._audio_path, lrc_text)
        else:
            self.failed.emit(self._track_title, "речь в треке не распознана")

    @staticmethod
    def _looks_like_speech(text):
        """Отсеивает мусор Whisper на инструменталах: «§», «♪♪» и прочее без букв."""
        return sum(ch.isalnum() for ch in text) >= 4

    @staticmethod
    def _clean_text(raw):
        """Убирает LRC-таймкоды и лишние пробелы, чтобы блок титров не пустовал."""
        text = re.sub(r"\[\d{1,2}:\d{1,2}(?:[.:]\d{1,3})?\]", " ", raw or "")
        return " ".join(text.split())


class WhisperInstaller(QThread):
    """Ставит Whisper в тот же Python, которым запущен плеер.

    Иначе пользователь ставит пакет в один интерпретатор, а плеер запускает другой —
    и текст песни «недоступен» при полностью установленном Whisper.
    """

    log = pyqtSignal(str)          # строка прогресса для статуса
    done = pyqtSignal(bool, str)   # успешно?, подробности

    def __init__(self, packages=WHISPER_PIP_PACKAGES, parent=None):
        super().__init__(parent)
        self._packages = tuple(packages)

    def command(self):
        """Команда установки: тот же интерпретатор + pip, без лишнего шума."""
        return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                *self._packages]

    def run(self):
        cmd = self.command()
        self.log.emit("запускаем pip…")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            self.done.emit(False, f"не удалось запустить pip: {exc}")
            return
        tail = []
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            del tail[:-4]                     # держим только последние строки
            shown = next((p.strip() for p in reversed(line.split("|")) if p.strip()), "")
            if shown:
                self.log.emit(shown[:80])
        code = proc.wait()
        if code == 0:
            self.done.emit(True, "установлено: " + ", ".join(self._packages))
        else:
            self.done.emit(False, " ".join(tail)[-400:] or f"pip завершился с кодом {code}")


VK_OFFICIAL_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def vk_error_ru(reason):
    """Переводит типичные отказы ВК при входе на понятный русский.

    ВК отвечает английскими текстами вида «Too many attempts were made to
    sign in…» — без перевода пользователь не понимает, что делать дальше.
    Неизвестные ошибки возвращаются как есть, чтобы ничего не потерять.
    """
    low = (reason or "").lower()
    if "too many attempts" in low or "слишком много" in low:
        return ("ВК ограничил попытки входа: слишком много попыток. "
                "Подождите 15–60 минут и НЕ нажимайте кнопку повторно — "
                "каждая новая попытка продлевает паузу. "
                "Перед повтором проверьте пароль, зайдя на vk.com в браузере.")
    if "invalid password" in low or "wrong password" in low:
        return "Неверный логин или пароль от ВК — проверьте раскладку и пробелы."
    if "captcha" in low:
        return ("ВК требует капчу — это бывает при подозрительной активности. "
                "Подождите час-два и повторите.")
    if "two-factor" in low or "two factor" in low or "2fa" in low:
        return ("ВК ждёт код двухфакторной авторизации — введите его "
                "в поле для SMS-кода ниже и нажмите кнопку ещё раз.")
    if "no internet" in low or "network is unreachable" in low \
            or "timed out" in low or "temporary failure" in low:
        return "Нет связи с ВК — проверьте интернет и повторите позже."
    return reason or "неизвестная ошибка"


def vk_error_is_flood(reason):
    """ВК действительно заблокировал вход из-за слишком многих попыток?"""
    low = (reason or "").lower()
    return "too many attempts" in low or "слишком много попыток" in low


def vk_error_is_bad_password(reason):
    """ВК отклонил сам пароль? Повтор тем же паролём ничего не даст."""
    low = (reason or "").lower()
    return "invalid password" in low or "wrong password" in low


class VkAudioTokenWorker(QThread):
    """Получает токен ВК для аудиоAPI автоматически, по логину и паролю.

    Библиотека vkaudiotoken умеет сама логиниться в официальное приложение ВК
    и в Kate Mobile и выдавать токен, который подходит для аудио. Это удобнее
    ручного создания приложения, но требует пароль от аккаунта.

    Пароль НЕ сохраняется: он нужен только в момент запроса токена и сразу
    стирается из памяти. Токен сохраняется, пароль — никогда.
    """

    # Готовый токен, либо понятная причина, почему не получился
    fetched = pyqtSignal(str, str, str)      # токен, user-agent, пояснение
    need_code = pyqtSignal(str)              # ВК просит код из СМС
    failed = pyqtSignal(str)                 # причина отказа

    _codes = (0, 1, 2, 3, 4, 5)              # коды ошибок TokenException

    def __init__(self, login, password, auth_code="GET_CODE", parent=None):
        super().__init__(parent)
        self._login = (login or "").strip()
        self._password = password or ""
        self._auth_code = auth_code or "GET_CODE"

    def _describe(self, code):
        """Человеческие названия кодов ошибок библиотеки."""
        return {
            0: "ошибка регистрации устройства — попробуйте позже",
            1: "токен не обновился, прежний ещё действителен",
            2: "ВК не отдал токен",
            3: "ошибка сети при обращении к ВК",
            4: "нужен код подтверждения из СМС",
            5: "ошибка двухфакторной авторизации",
        }.get(code, "неизвестная ошибка")

    def run(self):
        # Сначала проверяем данные вообще: без сети, сразу и без сюрпризов.
        if not self._login or not self._password:
            self.failed.emit("Введите логин и пароль от ВК")
            return
        try:
            import vkaudiotoken
        except ImportError:
            self.failed.emit(
                "Нет библиотеки vkaudiotoken — установите: pip install vkaudiotoken")
            return
        official_error = ""
        try:
            # Сначала официальное приложение ВК: токен работает с обычным API
            data = vkaudiotoken.get_vk_official_token(
                self._login, self._password, self._auth_code)
            token = (data or {}).get("token") or ""
            user_agent = (data or {}).get("user_agent") or VK_OFFICIAL_USER_AGENT
            if token:
                self.fetched.emit(token, user_agent,
                                  "Токен получен через официальное приложение ВК")
                return
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == 4:                     # ВК ждёт SMS
                self.need_code.emit(
                    "ВК прислал код на телефон — введите его ниже и повторите")
                return
            text = str(exc)
            if vk_error_is_flood(text):
                # ВК уже ограничил вход: второй запрос с тем же паролём лишь
                # продлит блокировку — запасным путём НЕЛЬЗЯ пользоваться.
                self.failed.emit(text)
                return
            if vk_error_is_bad_password(text):
                # Пароль отклонён аккаунтом — Kate с тем же паролём тоже не
                # войдёт, не будем жечь вторую попытку на пустом месте.
                self.failed.emit(text)
                return
            if code is not None and code not in self._codes:
                self.failed.emit(text)
                return
            official_error = text
        # Запасной путь — клиент Kate Mobile (даёт токен для аудиоAPI)
        try:
            data = vkaudiotoken.get_kate_token(
                self._login, self._password, self._auth_code)
            token = (data or {}).get("token") or ""
            user_agent = (data or {}).get("user_agent") or ""
            if token:
                self.fetched.emit(token, user_agent,
                                  "Токен получен через Kate Mobile (для аудио)")
                return
            self.failed.emit("Kate Mobile не вернул токен")
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == 4:
                self.need_code.emit(
                    "ВК прислал код на телефон — введите его ниже и повторите")
                return
            text = str(exc)
            if vk_error_is_flood(text):
                self.failed.emit(text)      # сырой текст — его поймёт переводчик
                return
            reason = self._describe(code) if code is not None else text
            if official_error and official_error != reason:
                reason = f"{reason} (офиц. приложение: {official_error})"
            self.failed.emit(f"Не удалось получить токен: {reason}")
            return
        self.failed.emit("Не удалось получить токен")


class VkTokenCheckWorker(QThread):
    """Проверяет токен ВК живым запросом к API — в фоне, чтобы окно не замирало."""

    checked = pyqtSignal(bool, str)     # рабочий ли токен, пояснение

    def __init__(self, token, parent=None):
        super().__init__(parent)
        self._token = (token or "").strip()

    def run(self):
        if not self._token:
            self.checked.emit(False, "Токен не введён")
            return
        # Заведомо пустой запрос: так возвращается список исполнителей,
        # и по ответу видно, пустил ли токен API или нет.
        url = ("https://api.vk.com/method/users.get?fields=screen_name"
               "&v=5.199&access_token=" + urllib.parse.quote(self._token))
        try:
            data = json.loads(_http_get(url, timeout=20, headers=_vk_headers()))
        except Exception as exc:
            self.checked.emit(False, f"Нет связи с ВК: {type(exc).__name__}")
            return
        if "error" in data:
            error = data["error"]
            self.checked.emit(False,
                              f"Токен отклонён: {error.get('error_msg', 'ошибка')}")
            return
        self.checked.emit(True, "Токен рабочий — поиск в VK Музыке доступен")


class PlatformSearchWorker(QThread):
    """Ищет треки на одной платформе через yt-dlp — по названию, без ссылки.

    У каждой платформы свой префикс поиска: scsearch — SoundCloud, ytsearch —
    YouTube. Префиксов, которых нет в yt-dlp (Яндекс.Музыка, VK, Spotify),
    намеренно не запрашиваем: они помечены can_search = False.
    """

    results = pyqtSignal(str, list)     # id платформы, список найденных треков
    failed = pyqtSignal(str, str)       # id платформы, причина

    def __init__(self, query, platform, count=SEARCH_RESULTS, parent=None):
        super().__init__(parent)
        self._query = (query or "").strip()
        self._platform = platform if platform in PLATFORM_BY_ID else "sc"
        self._count = max(1, min(int(count or 1), 10))

    def command(self):
        """Команда поиска: лучшие совпадения с названием, автором и ссылкой."""
        prefix = PLATFORM_BY_ID[self._platform]["prefix"] or "scsearch"
        return [
            sys.executable, "-m", "yt_dlp",
            f"{prefix}{self._count}:{self._query}",
            "--flat-playlist",
            "--no-warnings",
            "--ignore-errors",
            "--print", "%(title)s\t%(uploader)s\t%(webpage_url)s\t%(duration)s",
        ]

    @staticmethod
    def parse(stdout, platform="sc"):
        """Разбираем вывод yt-dlp: название, автор, ссылка, длительность.

        Платформа проставляется в каждый трек, чтобы в окне поиска было видно,
        откуда он найден: «Автор — Название (SoundCloud)».
        """
        found = []
        for line in (stdout or "").splitlines():
            parts = [part.strip() for part in line.split("\t")]
            if len(parts) < 3 or not parts[2].startswith("http"):
                continue
            title = "" if parts[0] in ("", "NA") else parts[0]
            author = "" if parts[1] in ("", "NA") else parts[1]
            duration = 0.0
            if len(parts) > 3 and parts[3] not in ("", "NA"):
                try:
                    duration = float(parts[3])
                except ValueError:
                    duration = 0.0
            found.append({"type": platform, "platform": platform,
                          "title": title or parts[2],
                          "author": author, "url": parts[2], "duration": duration})
        return found


    def run(self):
        if not self._query:
            # Молчание здесь остановило бы общий поиск: он ждёт ответа каждой
            # площадки, поэтому о пустом запросе тоже сообщаем.
            self.failed.emit(self._platform, "пустой запрос")
            return
        try:

            proc = subprocess.run(self.command(), capture_output=True, text=False,
                                  env=_ytdlp_env(), timeout=90)
        except subprocess.TimeoutExpired:
            self.failed.emit(self._platform, "не ответил за полторы минуты")
            return
        except FileNotFoundError:
            self.failed.emit(self._platform, "yt-dlp не установлен")
            return
        except Exception as exc:
            self.failed.emit(self._platform, f"{type(exc).__name__}: {exc}")
            return
        if self.isInterruptionRequested():
            return
        tracks = self.parse(_decode_ytdlp(proc.stdout), self._platform)
        if tracks:
            self.results.emit(self._platform, tracks)
            return
        # Причину берём из stderr: так видно и «нет сети», и «площадка недоступна»
        error_lines = [ln.strip() for ln in _decode_ytdlp(proc.stderr).splitlines()
                       if ln.strip()]
        self.failed.emit(self._platform, error_lines[-1] if error_lines
                         else "ничего не найдено")


class WebSearchWorker(QThread):
    """Ищет треки там, где у yt-dlp поиска нет: Яндекс.Музыка, ВК Музыка.

    Каждая площадка опрашивается по своему открытому способу (см. функции выше).
    Площадка, которая не ответила, просто не даёт результатов — она не должна
    портить поиск по остальным.
    """

    results = pyqtSignal(str, list)     # id платформы, найденные треки
    failed = pyqtSignal(str, str)       # id платформы, причина

    def __init__(self, query, platform, count=SEARCH_RESULTS, parent=None):
        super().__init__(parent)
        self._query = (query or "").strip()
        self._platform = platform if platform in PLATFORM_BY_ID else "sc"
        self._count = max(1, min(int(count or 1), 10))

    def fetch(self):
        """Реальный поиск на площадке. Возвращает список треков."""
        if self._platform == "ym":
            return search_yandex_music(self._query, self._count)
        if self._platform == "vk":
            return search_vk_music(self._query, self._count)
        if self._platform == "spotify":
            # По названию Spotify не ищет без авторизации — принимаем ссылку.
            return [spotify_track_info(self._query)] if detect_platform(self._query) else []
        return []

    def run(self):
        if not self._query:
            self.failed.emit(self._platform, "пустой запрос")
            return
        try:
            tracks = [t for t in self.fetch() if t]
        except RuntimeError as exc:
            self.failed.emit(self._platform, str(exc))
            return
        except Exception as exc:
            self.failed.emit(self._platform, f"{type(exc).__name__}: {exc}")
            return
        if self.isInterruptionRequested():
            return
        if tracks:
            self.results.emit(self._platform, tracks)
            return
        # Обязательно сообщаем и об отсутствии результата: общий поиск ждёт ответа
        # каждой площадки, и молчание здесь навсегда остановило бы весь поиск.
        if self._platform == "spotify" and not detect_platform(self._query):
            self.failed.emit(self._platform, "поиск по названию закрыт, нужна ссылка Spotify")
        elif self._platform == "vk" and not VK_ACCESS_TOKEN:
            self.failed.emit(self._platform, "нужен токен ВК (SVYAZ_VK_TOKEN)")
        else:
            self.failed.emit(self._platform, "ничего не найдено")



class SearchWorker(QObject):
    """Ищет трек сразу на нескольких платформах и собирает результаты вместе.

    Это НЕ QThread, а менеджер: он запускает по одному потоку на площадку из
    главного потока Qt. Так сделано намеренно — потоки, созданные внутри другого
    потока, теряли сигналы, и поиск намертво зависал.

    Каждая площадка опрашивается своим способом: через yt-dlp (SoundCloud,
    YouTube) или через собственный открытый источник (Яндекс.Музыка, ВК Музыка,
    Spotify). Медленная или недоступная площадка не задерживает остальные.
    """

    results = pyqtSignal(list)              # треки со всех платформ
    failed = pyqtSignal(str)                # причина, если не нашлось ничего
    platform_failed = pyqtSignal(str, str)  # платформа, её причина (для статуса)
    progress = pyqtSignal(str, int, int)    # площадок ответило, всего
    finished = pyqtSignal()                 # поиск завершён (очередь опубликована)

    _alive = []

    def __init__(self, query, count=SEARCH_RESULTS, platforms=None, parent=None):
        super().__init__(parent)
        self._query = (query or "").strip()
        self._count = max(1, min(int(count or 1), 10))
        wanted = tuple(platforms) if platforms else active_search_platforms()
        self._platforms = tuple(p for p in wanted if p in PLATFORM_BY_ID)
        self._workers = []
        self._lock = threading.Lock()
        self._state = None
        self._total = len(self._platforms)
        self._deadline = time.monotonic() + SEARCH_TIMEOUT_SEC
        SearchWorker._alive.append(self)

    def _forget(self):
        try:
            SearchWorker._alive.remove(self)
        except ValueError:
            pass

    def command(self):
        """Команда поиска по одной платформе (по умолчанию — SoundCloud)."""
        platform = self._platforms[0] if self._platforms else "sc"
        return PlatformSearchWorker(self._query, platform, self._count).command()

    @staticmethod
    def parse(stdout, platform="sc"):
        """Разбор вывода yt-dlp в список треков (см. PlatformSearchWorker.parse)."""
        return PlatformSearchWorker.parse(stdout, platform)

    def start(self):
        """Запускает поиск по всем площадкам. Вызывается из главного потока."""
        if not self._query:
            self.failed.emit("пустой запрос")
            self.finished.emit()
            return
        if not self._platforms:
            self.failed.emit("нет платформ для поиска")
            self.finished.emit()
            return
        # Результаты копим под замком, а наружу отдаём только из главного потока
        # (см. poll). Иначе слоты плеера выполнялись бы в чужом потоке.
        self._state = {"collected": [], "errors": [], "done": 0, "reported": 0}
        self._total = len(self._platforms)
        self._deadline = time.monotonic() + SEARCH_TIMEOUT_SEC
        self._workers = []

        def on_results(platform_id, tracks):
            with self._lock:
                self._state["collected"].extend(tracks)
                self._state["done"] += 1

        def on_failed(platform_id, reason):
            with self._lock:
                self._state["errors"].append((platform_id, reason))
                self._state["done"] += 1

        for platform_id in self._platforms:
            # Площадку опрашиваем тем способом, который для неё работает:
            # через yt-dlp или через собственный открытый источник.
            if PLATFORM_BY_ID[platform_id].get("prefix"):
                worker = PlatformSearchWorker(self._query, platform_id, self._count)
            else:
                worker = WebSearchWorker(self._query, platform_id, self._count)
            worker.results.connect(on_results)
            worker.failed.connect(on_failed)
            self._workers.append(worker)
            worker.start()
        if not self._workers:
            self.failed.emit("поиск не запустился")
            self.finished.emit()

    def isRunning(self):
        """Есть ли ещё работающие потоки площадок (для совместимости с QThread)."""
        return bool(self._state) and any(w.isRunning() for w in self._workers)

    def wait(self, timeout_ms=0):
        """Дождаться потоков площадок. Совместимость с прежним вызовом из closeEvent."""
        for worker in list(self._workers):
            try:
                worker.wait(timeout_ms or 1500)
            except RuntimeError:
                pass

    def requestInterruption(self):
        """Просим площадки прекратить работу (вызывается при закрытии плеера)."""
        self._deadline = 0
        for worker in self._workers:
            try:
                worker.requestInterruption()
            except RuntimeError:
                pass

    def poll(self):
        """Сторожевой таймер поиска. Работает в главном потоке Qt.

        Здесь и только здесь публикуются сигналы: так все слоты плеера
        гарантированно выполняются в главном потоке, как и требует Qt.
        """
        state = getattr(self, "_state", None)
        if state is None:
            return
        with self._lock:
            collected = list(state["collected"])
            errors = list(state["errors"])
            done, reported = state["done"], state["reported"]
        # Прогресс площадок — тоже из главного потока
        while reported < min(done, self._total):
            reported += 1
            self.progress.emit("площадок", reported, self._total)
        with self._lock:
            state["reported"] = reported
        # Дедлайн считаем по настоящим часам, а не числом вызовов: иначе при
        # частом опросе ожидание сгорало бы во много раз быстрее задуманного.
        now = time.monotonic()
        expired = now >= self._deadline
        if not (done >= self._total or expired):
            return
        if expired and done < self._total:
            console(f"Поиск: ответили {done} из {self._total} площадок, "
                    "показываем что успели")
        self._state = None
        self._emit_results(collected, errors)
        self.finished.emit()


    def _emit_results(self, collected, errors):
        """Публикует объединённый список: без дублей, с подписью платформы."""
        if collected:
            unique, seen = [], set()
            for track in collected:
                key = track.get("url")
                if key and key not in seen:
                    seen.add(key)
                    unique.append(track)
            self.results.emit(unique)
            return
        for platform_id, reason in errors:
            self.platform_failed.emit(platform_name(platform_id), reason)
        self.failed.emit(errors[0][1] if errors else "ничего не найдено")





class SearchWindow(QDialog):
    """Результаты поиска: двойной клик добавляет трек в плейлист и играет.

    Рядом с названием всегда указана платформа-источник в скобках:
    «Серега Пират — Прости я не знаю (SoundCloud)».
    """

    def __init__(self, parent, query, tracks):
        super().__init__(parent)
        self.p = parent
        self.query = query
        self.tracks = list(tracks)
        names = ", ".join(sorted({platform_name(t.get("platform") or t.get("type"))
                                  for t in self.tracks if t.get("url")
                                  and (t.get("platform") or t.get("type"))}))
        self.setWindowTitle(f"Поиск музыки — {query}")
        self.setGeometry(220, 200, 680, 420)
        self.lay = QVBoxLayout(self)

        hint = (f"Найдено: {len(self.tracks)}.")
        if names:
            hint += f" Платформы: {names}."
        self.hint = QLabel(
            hint + "\nДвойной клик по треку — добавить в плейлист и включить. "
                   "Или выделите трек и нажмите «Добавить в плейлист».", self)
        self.hint.setWordWrap(True)
        self.lay.addWidget(self.hint)


        self.list = QListWidget(self)
        for track in self.tracks:
            item = QListWidgetItem(self.describe(track))
            item.setToolTip(track.get("url", ""))
            self.list.addItem(item)
        # Одиночный клик только выделяет трек: в плейлист попадает по двойному
        # клику или по кнопке — иначе трек добавляется случайно.
        self.list.itemDoubleClicked.connect(self.add_and_play_by_item)
        self.lay.addWidget(self.list)

        row = QHBoxLayout()
        self.btn_add = QPushButton("➕ Добавить в плейлист", self)
        self.btn_add.clicked.connect(self.add_current)
        row.addWidget(self.btn_add)
        self.btn_play = QPushButton("▶ Добавить и включить", self)
        self.btn_play.clicked.connect(self.add_and_play_current)
        row.addWidget(self.btn_play)
        self.lay.addLayout(row)

        self.setStyleSheet(self.p.styleSheet())
        for button in self.findChildren(QPushButton):
            button.setStyleSheet(self.p.btn_style())

    @staticmethod
    def describe(track):
        """Строка результата: автор — название, платформа в скобках и длительность.

        Формат: «Серега Пират — Прости я не знаю (SoundCloud)  [3:21]».
        Подпись платформы обязательна — по ней видно, откуда взят трек.
        """
        who = track.get("author") or platform_name(track.get("platform")) or "SoundCloud"
        name = track.get("title") or track.get("url", "")
        duration = float(track.get("duration") or 0.0)
        tail = f"  [{int(duration) // 60}:{int(duration) % 60:02d}]" if duration else ""
        source = platform_name(track.get("platform") or track.get("type"))
        # Для ссылки на ВК/Яндекс/Spotify платформу тоже показываем
        if not source and track.get("url"):
            source = platform_name(detect_platform(track["url"]))
        label = f"({source})" if source else ""
        return f"{who} — {name}{' ' + label if label else ''}{tail}"

    def _current_item(self):
        item = self.list.currentItem()
        if item is None and self.list.count():
            item = self.list.item(0)
        return item

    def add_by_item(self, item):
        """Добавляем выбранный трек в плейлист."""
        index = self.list.row(item)
        if 0 <= index < len(self.tracks):
            self.p.add_search_result(self.tracks[index])

    def add_current(self):
        item = self._current_item()
        if item is not None:
            self.add_by_item(item)

    def add_and_play_by_item(self, item):
        self.add_by_item(item)
        index = self.list.row(item)
        if 0 <= index < len(self.tracks):
            self.p.play_track_by_url(self.tracks[index].get("url", ""))

    def add_and_play_current(self):
        item = self._current_item()
        if item is not None:
            self.add_and_play_by_item(item)


class SettingsWindow(QDialog):
    """Настройки внешнего вида: цвет каждой кнопки, шрифт, прозрачность, профили."""

    def __init__(self, parent):
        super().__init__(parent)
        self.p = parent
        self.setWindowTitle("Настройки и кастомизация")
        self.setGeometry(150, 120, 470, 660)
        root = QVBoxLayout(self)

        # Настроек много, окно не резиновое — прокручиваем весь список
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        inner = QWidget(scroll)
        self.lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        self.lay.addWidget(self._section("Цвет каждой кнопки отдельно"))
        self.color_buttons = {}
        for key, label in THEMEABLE_BUTTONS:
            row = QHBoxLayout()
            name_lbl = QLabel(label, inner)
            name_lbl.setMinimumWidth(160)
            row.addWidget(name_lbl)
            picker = QPushButton(self._swatch_text(key), inner)
            picker.clicked.connect(lambda _c=False, k=key: self.p.pick_button_color(k))
            row.addWidget(picker, 1)
            self.color_buttons[key] = picker
            self.lay.addLayout(row)
        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Общий акцент", inner))
        self.btn_accent = QPushButton("", inner)
        self.btn_accent.clicked.connect(self.p.choose_color)
        accent_row.addWidget(self.btn_accent, 1)
        self.lay.addLayout(accent_row)

        lyrics_row = QHBoxLayout()
        lyrics_row.addWidget(QLabel("Текст песни", inner))
        self.btn_lyrics_color = QPushButton("", inner)
        self.btn_lyrics_color.clicked.connect(self.p.choose_lyrics_color)
        lyrics_row.addWidget(self.btn_lyrics_color, 1)
        self.lay.addLayout(lyrics_row)

        self.lay.addWidget(self._section("Шрифт и прозрачность"))
        font_row = QHBoxLayout()
        font_row.addWidget(QLabel("Шрифт", inner))
        self.btn_font = QPushButton(self.p.current_font, inner)
        self.btn_font.clicked.connect(self.p.change_font_style)
        font_row.addWidget(self.btn_font, 1)
        self.lay.addLayout(font_row)

        self.lbl = QLabel("Прозрачность интерфейса:", inner)
        self.lay.addWidget(self.lbl)
        self.sld = QSlider(Qt.Orientation.Horizontal, inner)
        self.sld.setMinimum(20)
        self.sld.setMaximum(100)
        self.sld.setValue(int(self.p.current_opacity * 100))
        self.sld.valueChanged.connect(self.p.change_opacity)
        self.lay.addWidget(self.sld)

        self.lay.addWidget(self._section("Фон"))
        self.btn_gif = QPushButton("🎞 Загрузить гифку на фон", inner)
        self.btn_gif.clicked.connect(self.p.choose_gif)
        self.lay.addWidget(self.btn_gif)
        self.btn_img = QPushButton("🖼 Загрузить Фото на фон", inner)
        self.btn_img.clicked.connect(self.p.choose_image)
        self.lay.addWidget(self.btn_img)
        self.btn_blur = QPushButton("🌫 Размыть фон (Вкл/Выкл)", inner)
        self.btn_blur.clicked.connect(self.p.toggle_blur)
        self.lay.addWidget(self.btn_blur)

        self.lay.addWidget(self._section("Профили кастомизации"))
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Профиль", inner))
        self.profile_box = QComboBox(inner)
        self.profile_box.setMinimumWidth(210)
        profile_row.addWidget(self.profile_box, 1)
        self.lay.addLayout(profile_row)
        self.btn_apply_profile = QPushButton("📂 Выбрать сохранённую", inner)
        self.btn_apply_profile.clicked.connect(self.apply_profile)
        self.lay.addWidget(self.btn_apply_profile)
        self.btn_save_theme = QPushButton("💾 Сохранить кастомизацию", inner)
        self.btn_save_theme.clicked.connect(self.p.save_theme_clicked)
        self.lay.addWidget(self.btn_save_theme)
        self.btn_reset_theme = QPushButton("♻ Сбросить к умолчанию", inner)
        self.btn_reset_theme.clicked.connect(self.p.reset_theme)
        self.lay.addWidget(self.btn_reset_theme)

        self.lay.addWidget(self._section("Текст песни: токен Genius"))
        hint = QLabel("Токен Genius (для точного текста с Genius.com):", inner)
        hint.setWordWrap(True)
        self.lay.addWidget(hint)
        self.genius_edit = QLineEdit(lyrics_web.load_token(), inner)
        self.genius_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.genius_edit.setPlaceholderText("вставьте токен с genius.com/api-clients")
        self.lay.addWidget(self.genius_edit)
        self.btn_token = QPushButton("🔑 Сохранить токен", inner)
        self.btn_token.clicked.connect(self.p.save_genius_token)
        self.lay.addWidget(self.btn_token)

        self.lay.addWidget(self._section("Токен ВК: получить автоматически"))
        auto_hint = QLabel(
            "Проще, чем создавать приложение вручную: введите логин и пароль от "
            "ВК — плеер сам получит токен для аудио (через официальное "
            "приложение или Kate Mobile).<br><br>"
            "<b>Пароль нигде не сохраняется</b> — он нужен только в момент "
            "входа и сразу стирается. Сохраняется только токен.<br>"
            "Если у вас включено подтверждение по SMS, придёт код — введите "
            "его в поле ниже и нажмите кнопку ещё раз.", inner)
        auto_hint.setWordWrap(True)
        self.lay.addWidget(auto_hint)

        self.vk_login_edit = QLineEdit(inner)
        self.vk_login_edit.setPlaceholderText("логин, телефон или e-mail от ВК")
        self.lay.addWidget(self.vk_login_edit)

        self.vk_password_edit = QLineEdit(inner)
        self.vk_password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vk_password_edit.setPlaceholderText("пароль от ВК (не сохраняется)")
        self.lay.addWidget(self.vk_password_edit)

        self.vk_code_edit = QLineEdit(inner)
        self.vk_code_edit.setPlaceholderText("код из СМС, если ВК его попросит")
        self.lay.addWidget(self.vk_code_edit)

        self.btn_vk_auto = QPushButton("🔐 Получить токен автоматически", inner)
        self.btn_vk_auto.clicked.connect(self.p.fetch_vk_token_auto)
        self.lay.addWidget(self.btn_vk_auto)

        self.lay.addWidget(self._section("Токен ВК: вручную"))
        vk_hint = QLabel(
            "Токен ВК нужен, чтобы искать треки в VK Музыке. Без него поиск "
            "работает по SoundCloud, YouTube и Яндекс.Музыке.\n"
            "<b>Как получить токен:</b><br>"
            "1. Войдите в ВК и откройте страницу приложений (кнопка ниже).<br>"
            "2. Нажмите «Создать приложение». Тип выберите <b>Standalone</b> — "
            "это приложение для компьютера. Имя любое, например «СвязьКлауд».<br>"
            "3. Откройте «Настройки» → вкладка «Ключи API».<br>"
            "4. Нажмите «Создать новый ключ» — появится строка вида "
            "<b>vk1.a.XXXX…</b>. Это и есть токен.<br>"
            "5. Вставьте её в поле ниже и нажмите «Сохранить токен ВК».<br><br>"
            "<b>Если ключ не создаётся</b>, надёжнее забрать токен через "
            "ссылку авторизации: откройте "
            "<b>https://oauth.vk.com/authorize?client_id=ВАШ_ID_ПРИЛОЖЕНИЯ"
            "&amp;scope=audio&amp;redirect_uri=https://oauth.vk.com/blank.html"
            "&amp;display=page&amp;response_type=token</b>, где ВАШ_ID_ПРИЛОЖЕНИЯ "
            "— это «ID приложения» на той же вкладке «Ключи API». После "
            "разрешения браузер покажет адрес вида "
            "<b>blank.html#access_token=vk1.a.XXXX…</b> — скопируйте только "
            "часть после <b>access_token=</b>.<br><br>"
            "Токен — это секрет: не пересылайте его никому. Плеер хранит его "
            "локально в .sc_cache/vk_token.txt и никуда не отправляет, кроме "
            "самого ВК.", inner)
        vk_hint.setWordWrap(True)
        self.vk_hint = vk_hint          # подсказкой пользуется и автотест
        self.lay.addWidget(vk_hint)
        self.btn_vk_help = QPushButton("🌐 Открыть страницу приложений ВК", inner)
        self.btn_vk_help.clicked.connect(self.p.open_vk_apps)
        self.lay.addWidget(self.btn_vk_help)

        self.vk_token_edit = QLineEdit(self.p.vk_token, inner)
        self.vk_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vk_token_edit.setPlaceholderText("вставьте access_token ВК")
        self.lay.addWidget(self.vk_token_edit)
        self.btn_vk_token = QPushButton("🔑 Сохранить токен ВК", inner)
        self.btn_vk_token.clicked.connect(self.p.save_vk_token)
        self.lay.addWidget(self.btn_vk_token)
        self.btn_vk_check = QPushButton("✅ Проверить токен", inner)
        self.btn_vk_check.setToolTip(
            "Пробует найти треки в VK Музыке. Если ответ пришёл — токен рабочий.")
        self.btn_vk_check.clicked.connect(self.p.check_vk_token)
        self.lay.addWidget(self.btn_vk_check)

        self.lay.addStretch(1)
        self.refresh_profiles()
        self.update_theme()

    @staticmethod
    def _section(title):
        """Заголовок группы настроек — отделяет блоки друг от друга."""
        lbl = QLabel(f"<b>{title}</b>")
        lbl.setContentsMargins(0, 8, 0, 2)
        return lbl

    def _swatch_text(self, key):
        """Кнопка-образец: подпись показывает текущий цвет кнопки."""
        color = (self.p.button_color(key) if key != "__accent__"
                 else self.p.current_accent_color)
        return f"Цвет: {color}"

    def refresh_profiles(self):
        """Перечитывает список сохранённых профилей в выпадающий список."""
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        self.profile_box.addItem("— выберите профиль —", "")
        for name in self.p.theme_profiles():
            self.profile_box.addItem(name, name)
        index = self.profile_box.findData(self.p.current_profile)
        if index >= 0:
            self.profile_box.setCurrentIndex(index)
        self.profile_box.blockSignals(False)

    def apply_profile(self):
        """Кнопка «Выбрать сохранённую» — применяет выбранный профиль."""
        name = self.profile_box.currentData()
        if not name:
            self.p.status_lbl.setText("Статус: Сначала выберите профиль в списке")
            return
        if self.p.apply_theme_profile(name):
            self.p.status_lbl.setText(f"Статус: Профиль «{name}» применён")
            self.update_theme()

    def update_theme(self):
        """Перерисовывает окно под текущую тему и обновляет образцы цветов."""
        self.setStyleSheet(self.p.styleSheet())
        for b in self.findChildren(QPushButton):
            b.setStyleSheet(self.p.btn_style())
        for key, picker in self.color_buttons.items():
            color = self.p.button_color(key)
            picker.setText(f"{THEME_LABELS.get(key, key)}: {color}")
            picker.setStyleSheet(
                f"background-color: {color}; color: #ffffff; border: none; "
                f"border-radius: 4px; padding: 6px 8px; font-weight: bold;")
        for button, color in ((self.btn_accent, self.p.current_accent_color),
                              (self.btn_lyrics_color, self.p.current_lyrics_color)):
            button.setText(f"Цвет: {color}")
            button.setStyleSheet(
                f"background-color: {color}; color: #ffffff; border: none; "
                f"border-radius: 4px; padding: 6px 8px; font-weight: bold;")
        self.btn_font.setText(self.p.current_font)


class SvyazCloudPlayerCustom(QWidget):
    def __init__(self):
        super().__init__()
        # Сначала поднимаем сохранённую кастомизацию: цвета, шрифты, прозрачность
        # и фоны должны быть готовы до того, как создадутся кнопки и панели.
        saved = load_theme_config()
        self.current_accent_color = saved["accent"]
        self.current_lyrics_color = saved["lyrics_color"]
        self.current_opacity = saved["opacity"]
        self.current_font = saved["font"]
        self.current_volume = saved["volume"]
        self.button_colors = dict(saved["buttons"])
        self.current_profile = ""
        self.background_source = saved["background"]
        # Токен ВК подгружаем сразу: без него VK Музыка не ищется вовсе
        self.vk_token = load_vk_token()
        self._vk_check_thread = None   # текущая проверка токена
        self._vk_auto_thread = None    # текущий авто-вход в ВК
        self._vk_auto_cooldown = False  # ВК ограничил вход — кнопку не трогаем
        self.vk_user_agent = load_vk_user_agent()
        if self.vk_token:
            global VK_ACCESS_TOKEN
            VK_ACCESS_TOKEN = self.vk_token
        self.cover_size = 180
        self.is_paused = False
        self.is_playing = False
        self.is_repeat = False
        self.current_index = None
        self.current_url = ""
        self._was_busy = False
        self._user_seeking = False
        self._track_duration = 0.0
        self._track_offset = 0.0
        self._play_start_ticks = 0
        self._ui_queue = queue.Queue()
        self._whisper_thread = None      # текущий поток распознавания текста
        self._whisper_key = ""           # аудиофайл, который распознаётся сейчас
        self._web_thread = None          # текущий поток поиска текста в интернете
        self._web_key = ""               # аудиофайл, который ищется сейчас
        self._whisper_hint_shown = False # подсказку про установку печатаем один раз
        self._whisper_asked = False     # диалог установки предлагаем один раз за сеанс
        self._installer = None          # текущий поток установки Whisper
        self._install_box = None        # окно предложения установки
        self._lyrics_memory = {}         # текст по файлу, чтобы не читать диск дважды
        self._lrc_memory = {}            # LRC-таймкоды по файлу трека
        self.font_styles = ["Segoe UI", "Courier New", "Impact", "Comic Sans MS"]
        try:
            self.current_font_index = max(0, self.font_styles.index(self.current_font))
        except ValueError:
            self.current_font_index = 0
        # Плейлисты — ДО создания интерфейса: комбобокс в init_ui уже читает их.
        self.playlists = {DEFAULT_PLAYLIST_NAME: []}
        self.active_playlist_name = DEFAULT_PLAYLIST_NAME
        self.playlist_tracks = self.playlists[self.active_playlist_name]
        self._playlist_loading = False
        self.init_ui()
        pygame.mixer.init()
        pygame.mixer.music.set_volume(self.current_volume)
        self.current_audio_file = ""
        self.blur_enabled = False
        self._search_thread = None       # текущий поиск по платформам
        self.search_window = None        # окно с результатами поиска
        # Фон из сохранённого профиля ставим после создания интерфейса
        self.apply_background_source()
        if saved["blur"]:
            self.toggle_blur()
        # Восстанавливаем плейлист из playlist.json — список переживает перезапуск
        restored = self.load_playlist()
        if not restored:
            self.status_lbl.setText("Статус: Ожидание треков")

    # --- Кастомизация: цвета кнопок, профили, сохранение ---------------------
    def button_color(self, key):
        """Цвет конкретной кнопки; если её не трогали — берётся общий акцент."""
        return self.button_colors.get(key) or self.current_accent_color

    def pick_button_color(self, key):
        """Палитра для одной кнопки: меняет её цвет, не трогая остальные."""
        label = THEME_LABELS.get(key, key)
        color = QColorDialog.getColor(QColor(self.button_color(key)), self,
                                      f"Цвет кнопки «{label}»")
        if color.isValid():
            self.button_colors[key] = color.name()
            self.current_profile = ""
            self.apply_custom_theme()
            self.status_lbl.setText(f"Статус: Цвет кнопки «{label}» изменён")
            self._refresh_settings()

    def choose_lyrics_color(self):
        """Палитра для цвета текста песни."""
        color = QColorDialog.getColor(QColor(self.current_lyrics_color), self,
                                      "Цвет текста песни")
        if color.isValid():
            self.current_lyrics_color = color.name()
            self.current_profile = ""
            self.apply_custom_theme()
            self.status_lbl.setText("Статус: Цвет текста песни изменён")
            self._refresh_settings()

    def _refresh_settings(self):
        """Обновляет уже открытое окно настроек, не создавая второе."""
        win = getattr(self, "settings_win", None)
        if win is not None and win.isVisible():
            win.update_theme()

    def theme_snapshot(self):
        """Текущее оформление обычным словарём — именно его пишем в файл."""
        return {
            "accent": self.current_accent_color,
            "lyrics_color": self.current_lyrics_color,
            "opacity": round(float(self.current_opacity), 3),
            "font": self.current_font,
            "font_index": int(getattr(self, "current_font_index", 0)),
            "background": self.background_source,
            "blur": bool(self.blur_enabled),
            "volume": round(float(self.current_volume), 3),
            "buttons": dict(self.button_colors),
        }

    def _read_theme_file(self):
        """Читает theme_config.json целиком (включая список пресетов)."""
        try:
            with open(theme_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def theme_profiles(self):
        """Имена всех сохранённых профилей."""
        presets = self._read_theme_file().get("presets")
        return sorted(presets) if isinstance(presets, dict) else []

    def save_theme_clicked(self):
        """Кнопка «Сохранить кастомизацию»: спрашивает имя и пишет theme_config.json."""
        existing = self.theme_profiles()
        suggested = self.current_profile or (existing[0] if existing else "Мой")
        name, ok = QInputDialog.getText(self, "Сохранить кастомизацию",
                                        "Имя профиля:", text=suggested)
        name = (name or "").strip()
        if not ok or not name:
            self.status_lbl.setText("Статус: Сохранение отменено")
            return False
        return self.save_theme_profile(name)

    def save_theme_profile(self, name):
        """Записывает профиль в theme_config.json, не затирая остальные."""
        data = self._read_theme_file()
        presets = data.get("presets")
        if not isinstance(presets, dict):
            presets = {}
        presets[name] = self.theme_snapshot()
        payload = {"version": 1, "current": name, "presets": presets}
        try:
            with open(theme_path(), "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            console(f"Кастомизация не сохранена: {exc}")
            self.status_lbl.setText("Статус: Не удалось сохранить кастомизацию")
            return False
        self.current_profile = name
        self.status_lbl.setText(f"Статус: Кастомизация сохранена — профиль «{name}»")
        win = getattr(self, "settings_win", None)
        if win is not None and win.isVisible():
            win.refresh_profiles()
        return True

    def apply_theme_profile(self, name):
        """Загружает сохранённый профиль и применяет его к окну."""
        presets = self._read_theme_file().get("presets")
        if not isinstance(presets, dict) or name not in presets:
            self.status_lbl.setText("Статус: Профиль не найден")
            return False
        data = presets[name]
        if not isinstance(data, dict):
            return False
        self.current_accent_color = _color_name(data.get("accent"), self.current_accent_color)
        self.current_lyrics_color = _color_name(data.get("lyrics_color"),
                                                self.current_lyrics_color)
        try:
            self.current_opacity = max(0.2, min(1.0, float(data.get("opacity", 0.9))))
        except (TypeError, ValueError):
            pass
        try:
            self.current_volume = max(0.0, min(1.0, float(data.get("volume", 0.7))))
        except (TypeError, ValueError):
            pass
        font = str(data.get("font") or "Segoe UI")
        self.current_font = font
        if font in self.font_styles:
            self.current_font_index = self.font_styles.index(font)
        buttons = data.get("buttons")
        self.button_colors = ({str(k): _color_name(v, self.current_accent_color)
                               for k, v in buttons.items()}
                              if isinstance(buttons, dict) else {})
        self.current_profile = name
        self.apply_background_source(str(data.get("background") or ""))
        if bool(data.get("blur")) and not self.blur_enabled:
            self.toggle_blur()
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(int(self.current_volume * 100))
        self.volume_slider.blockSignals(False)
        win = getattr(self, "settings_win", None)
        if win is not None and win.isVisible():
            win.sld.blockSignals(True)
            win.sld.setValue(int(self.current_opacity * 100))
            win.sld.blockSignals(False)
        self.apply_custom_theme()
        return True

    def reset_theme(self):
        """Возвращает оформление к значениям по умолчанию."""
        self.current_accent_color = DEFAULT_THEME["accent"]
        self.current_lyrics_color = DEFAULT_THEME["lyrics_color"]
        self.current_opacity = DEFAULT_THEME["opacity"]
        self.current_font = DEFAULT_THEME["font"]
        self.current_font_index = 0
        self.button_colors = {}
        self.current_profile = ""
        self.apply_background_source("")
        self.apply_custom_theme()
        self.status_lbl.setText("Статус: Настройки сброшены к умолчаниям")
        self._refresh_settings()

    def apply_background_source(self, path=None):
        """Ставит фон из сохранённого пути: гифку или картинку."""
        if path is not None:
            self.background_source = path or ""
        if not self.background_source or not os.path.exists(self.background_source):
            self.background_source = ""
            self.bg_label.setMovie(None)
            self.bg_label.clear()
            return False
        self.bg_label.setPixmap(QPixmap())
        if self.background_source.lower().endswith(".gif"):
            movie = QMovie(self.background_source)
            self.bg_label.setMovie(movie)
            movie.start()
        else:
            self.bg_label.setMovie(None)
            self.bg_label.setPixmap(QPixmap(self.background_source))
        return True


    def init_ui(self):
        self.setWindowTitle("СвязьКлауд")
        self.setGeometry(100, 100, 1020, 620)
        self.setMinimumSize(920, 560)

        # Фоновое фото/GIF — самый нижний слой окна
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(0, 0, 1020, 620)
        self.bg_label.setScaledContents(True)
        self.bg_label.lower()

        # === ГЛАВНАЯ ГОРИЗОНТАЛЬНАЯ СЕТКА: слева Now Playing, справа плеер ===
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(16)

        # ================= ЛЕВАЯ ПАНЕЛЬ «NOW PLAYING» =================
        self.now_playing_panel = QWidget(self)
        self.now_playing_panel.setObjectName("NowPlayingPanel")
        self.now_layout = QVBoxLayout(self.now_playing_panel)
        self.now_layout.setContentsMargins(18, 18, 18, 18)
        self.now_layout.setSpacing(12)

        # Верхний ряд: обложка слева, автор и название — справа от неё
        self.top_row = QHBoxLayout()
        self.top_row.setSpacing(16)

        # Большой квадратный блок под обложку трека (слева, а не по центру)
        self.cover_label = QLabel("♪", self.now_playing_panel)
        self.cover_label.setObjectName("CoverLabel")
        self.cover_label.setFixedSize(self.cover_size, self.cover_size)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.top_row.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignTop)

        # Крупный автор и название трека — правее обложки
        self.info_layout = QVBoxLayout()
        self.info_layout.setSpacing(6)

        self.author_label = QLabel("Автор", self.now_playing_panel)
        self.author_label.setObjectName("AuthorLabel")
        self.author_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.author_label.setWordWrap(True)
        self.author_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.author_label.setMinimumWidth(200)
        self.info_layout.addWidget(self.author_label)

        self.track_label = QLabel("Название трека", self.now_playing_panel)
        self.track_label.setObjectName("TrackLabel")
        self.track_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.track_label.setWordWrap(True)
        self.track_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.track_label.setMinimumWidth(200)
        self.info_layout.addWidget(self.track_label)

        self.info_layout.addStretch(1)
        self.top_row.addLayout(self.info_layout, 1)
        self.now_layout.addLayout(self.top_row)

        # Графическая дорожка (вейвформа) — сразу под обложкой
        self.waveform = WaveformWidget(self.now_playing_panel)
        self.waveform.setObjectName("Waveform")
        self.now_layout.addWidget(self.waveform)

        self.now_layout.addStretch(1)

        # Текст песни (караоке/lyrics) — 4 строки без панели, ближе к центру.
        # Пока трек не играет, блок пустой — никаких надписей-подсказок
        self.lyrics_label = LyricsMarquee(4, self.now_playing_panel)
        self.lyrics_label.setObjectName("LyricsLabel")
        self.now_layout.addWidget(self.lyrics_label)
        self.now_layout.addStretch(1)

        self.main_layout.addWidget(self.now_playing_panel, 4)

        # ================= ПРАВАЯ ПАНЕЛЬ: ПЛЕЙЛИСТ И УПРАВЛЕНИЕ =================
        self.right_panel = QWidget(self)
        self.right_panel.setObjectName("RightPanel")
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(10)

        self.title_layout = QHBoxLayout()
        self.title_lbl = QLabel("СВЯЗЬКЛАУД", self.right_panel)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_layout.addWidget(self.title_lbl)

        self.btn_settings = QPushButton("⚙️", self.right_panel)
        self.btn_settings.setFixedSize(40, 40)
        self.btn_settings.clicked.connect(self.open_settings)
        self.title_layout.addWidget(self.btn_settings)
        self.right_layout.addLayout(self.title_layout)

        self.url_row = QHBoxLayout()
        self.url_input = QLineEdit(self.right_panel)
        self.url_input.setPlaceholderText(
            "ссылка (SoundCloud, YouTube, ВК, Яндекс.Музыка, Spotify) или название")
        self.url_row.addWidget(self.url_input, 1)

        self.btn_search = QPushButton("🔍 Поиск", self.right_panel)
        self.btn_search.setToolTip(
            "Ищет сразу на всех площадках: " + ", ".join(
                platform_name(p) for p in SEARCH_ALL_PLATFORMS))
        self.btn_search.clicked.connect(self.search_soundcloud)
        self.url_row.addWidget(self.btn_search)
        self.right_layout.addLayout(self.url_row)
        # Enter: ссылка — добавим в плейлист, обычный текст — ищем по всем площадкам
        self.url_input.returnPressed.connect(self.add_to_playlist)

        self.btn_layout = QHBoxLayout()
        self.btn_add_playlist = QPushButton("Плейлист", self.right_panel)
        self.btn_add_playlist.clicked.connect(self.add_to_playlist)
        self.btn_layout.addWidget(self.btn_add_playlist)

        self.btn_play_now = QPushButton(" Запустить сразу", self.right_panel)
        self.btn_play_now.clicked.connect(self.play_now_from_input)
        self.btn_layout.addWidget(self.btn_play_now)

        self.btn_local = QPushButton("📁 С ПК", self.right_panel)
        self.btn_local.clicked.connect(self.play_local_file)
        self.btn_layout.addWidget(self.btn_local)
        self.right_layout.addLayout(self.btn_layout)

        self.playlist_top_row = QHBoxLayout()
        self.playlist_lbl = QLabel("Плейлист:", self.right_panel)
        self.playlist_top_row.addWidget(self.playlist_lbl)

        self.playlist_box = QComboBox(self.right_panel)
        self.playlist_box.setToolTip("Выбор плейлиста — каждый хранится отдельно")
        self.playlist_box.currentTextChanged.connect(self._on_playlist_chosen)
        self.playlist_top_row.addWidget(self.playlist_box, 1)

        self.btn_playlist_new = QPushButton("➕", self.right_panel)
        self.btn_playlist_new.setToolTip("Новый плейлист")
        self.btn_playlist_new.clicked.connect(self.create_playlist_dialog)
        self.playlist_top_row.addWidget(self.btn_playlist_new)

        self.btn_playlist_delete = QPushButton("🗑", self.right_panel)
        self.btn_playlist_delete.setToolTip("Удалить текущий плейлист")
        self.btn_playlist_delete.clicked.connect(self.delete_playlist_dialog)
        self.playlist_top_row.addWidget(self.btn_playlist_delete)
        self.right_layout.addLayout(self.playlist_top_row)

        self.playlist_widget = QListWidget(self.right_panel)
        self.playlist_widget.setIconSize(QSize(40, 40))
        self.playlist_widget.itemDoubleClicked.connect(self.play_selected_from_playlist)
        # Правый клик по треку — меню с удалением из плейлиста
        self.playlist_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.playlist_widget.customContextMenuRequested.connect(self._show_playlist_menu)
        self.right_layout.addWidget(self.playlist_widget)

        # Кнопки работы со списком: удалить трек и сохранить плейлист
        self.list_layout = QHBoxLayout()
        self.btn_remove_track = QPushButton("❌ Удалить из плейлиста", self.right_panel)
        self.btn_remove_track.clicked.connect(self.remove_selected_track)
        self.list_layout.addWidget(self.btn_remove_track)

        self.btn_save_list = QPushButton("💾 Сохранить список", self.right_panel)
        self.btn_save_list.clicked.connect(self.save_playlist_clicked)
        self.list_layout.addWidget(self.btn_save_list)
        self.right_layout.addLayout(self.list_layout)

        # Импорт/экспорт и слияние плейлистов
        self.playlist_io_layout = QHBoxLayout()
        self.btn_playlist_import = QPushButton("📥 Импорт", self.right_panel)
        self.btn_playlist_import.setToolTip(
            "Импорт плейлиста из файла (JSON, M3U, TXT): как новый либо всё в текущий")
        self.btn_playlist_import.clicked.connect(self.import_playlist_dialog)
        self.playlist_io_layout.addWidget(self.btn_playlist_import)

        self.btn_playlist_export = QPushButton("📤 Экспорт", self.right_panel)
        self.btn_playlist_export.setToolTip("Сохранить текущий плейлист в файл (JSON/M3U)")
        self.btn_playlist_export.clicked.connect(self.export_playlist_dialog)
        self.playlist_io_layout.addWidget(self.btn_playlist_export)

        self.btn_playlist_merge = QPushButton("➕➕ Всё в текущий", self.right_panel)
        self.btn_playlist_merge.setToolTip(
            "Добавить все треки из другого плейлиста в текущий")
        self.btn_playlist_merge.clicked.connect(self.merge_playlist_dialog)
        self.playlist_io_layout.addWidget(self.btn_playlist_merge)
        self.right_layout.addLayout(self.playlist_io_layout)

        self.controls_layout = QHBoxLayout()
        self.btn_prev = QPushButton("⏮ Назад", self.right_panel)
        self.btn_prev.clicked.connect(self.play_prev_track)
        self.controls_layout.addWidget(self.btn_prev)

        self.btn_pause = QPushButton("⏸ Пауза", self.right_panel)
        self.btn_pause.setToolTip("Пауза и продолжение. Останавливает и запускает заново")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.controls_layout.addWidget(self.btn_pause)

        # Кнопки «Остановить музыку» больше нет: её роль выполняет «Пауза»,
        # которая меняет надпись и останавливает звук при повторном нажатии.
        self.btn_next = QPushButton("Вперед ⏭", self.right_panel)

        self.btn_next.clicked.connect(self.play_next_track)
        self.controls_layout.addWidget(self.btn_next)

        self.btn_repeat = QPushButton("🔁 Повтор", self.right_panel)
        self.btn_repeat.setCheckable(True)
        self.btn_repeat.clicked.connect(self.toggle_repeat)
        self.controls_layout.addWidget(self.btn_repeat)

        self.right_layout.addLayout(self.controls_layout)

        # Таймлайн / Шкала времени трека (слайдер прокрутки)
        self.timeline_layout = QHBoxLayout()
        self.time_cur_lbl = QLabel("00:00", self.right_panel)
        self.time_cur_lbl.setFixedWidth(44)
        self.time_cur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.timeline_layout.addWidget(self.time_cur_lbl)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal, self.right_panel)
        self.timeline_slider.setObjectName("TimelineSlider")
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(1000)
        self.timeline_slider.setValue(0)
        self.timeline_slider.sliderPressed.connect(self._on_seek_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_seek_released)
        self.timeline_slider.sliderMoved.connect(self._on_seek_moved)
        self.timeline_layout.addWidget(self.timeline_slider, 1)

        self.time_total_lbl = QLabel("00:00", self.right_panel)
        self.time_total_lbl.setFixedWidth(44)
        self.time_total_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.timeline_layout.addWidget(self.time_total_lbl)
        self.right_layout.addLayout(self.timeline_layout)

        # Громкость
        self.volume_layout = QHBoxLayout()
        self.volume_lbl = QLabel("🔊", self.right_panel)
        self.volume_layout.addWidget(self.volume_lbl)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal, self.right_panel)
        self.volume_slider.setObjectName("VolumeSlider")
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(int(self.current_volume * 100))
        self.volume_slider.valueChanged.connect(self.change_volume)
        self.volume_layout.addWidget(self.volume_slider, 1)

        self.volume_val_lbl = QLabel(f"{int(self.current_volume * 100)}%", self.right_panel)
        self.volume_val_lbl.setFixedWidth(44)
        self.volume_val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.volume_layout.addWidget(self.volume_val_lbl)
        self.right_layout.addLayout(self.volume_layout)

        self.status_lbl = QLabel("Статус: Ожидание треков", self.right_panel)
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.right_layout.addWidget(self.status_lbl)

        self.main_layout.addWidget(self.right_panel, 6)

        # Привязываем кнопки к ключам темы: по ключу ищется индивидуальный цвет
        for key, _label in THEMEABLE_BUTTONS:
            button = getattr(self, key, None)
            if button is not None:
                button.theme_key = key

        self.font_styles = ["Segoe UI", "Courier New", "Impact", "Comic Sans MS"]
        self.current_font_index = 0

        # Сообщения из фонового потока-загрузчика обновляют интерфейс здесь
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._drain_ui_queue)
        self._ui_timer.timeout.connect(self._check_track_end)
        self._ui_timer.timeout.connect(self._update_timeline_playback)
        self._ui_timer.start(100)
        # Сторож мультиплатформенного поиска: следит, чтобы площадка не молчала вечно
        self._search_timer = QTimer(self)
        self._search_timer.timeout.connect(self._poll_search)
        self._search_timer.start(SEARCH_POLL_MS)
        self.apply_custom_theme()


    def btn_style(self, key=None):
        """Стиль кнопки: своя заливка, если цвет задан, иначе — общий акцент."""
        color = self.button_color(key) if key else self.current_accent_color
        return f"background-color: {color}; color: #ffffff; border: none; border-radius: 4px; padding: 8px 12px; font-weight: bold; font-family: '{self.current_font}';"

    def _button_style_for(self, button):
        """Подбирает стиль кнопке по её ключу из THEMEABLE_BUTTONS."""
        key = getattr(button, "theme_key", None)
        if button is getattr(self, "btn_repeat", None) and self.is_repeat:
            return self._repeat_btn_style()
        return self.btn_style(key)


    def _repeat_btn_style(self):
        if getattr(self, "is_repeat", False):
            return f"background-color: #00ffff; color: #100b1a; border: 2px solid #ffffff; border-radius: 4px; padding: 7px 11px; font-weight: bold; font-family: '{self.current_font}';"
        return self.btn_style()

    def apply_custom_theme(self):
        alpha = int(self.current_opacity * 255)
        bg = f"rgba(13, 10, 18, {alpha})"; in_bg = f"rgba(26, 20, 36, {alpha})"; ls_bg = f"rgba(20, 15, 28, {alpha})"
        panel_bg = f"rgba(22, 15, 33, {alpha})"
        self.setStyleSheet(
            f"QWidget {{ background-color: {bg}; color: #ffffff; font-family: '{self.current_font}'; }} "
            # Метки не должны получать рамку от QWidget: блок с названием и автором
            # должен быть частью фиолетовой панели, а не рамкой вокруг текста.
            f"QLabel {{ background: transparent; border: none; }} "
            f"QWidget#NowPlayingPanel {{ background-color: {panel_bg}; border: none; border-radius: 14px; }} "
            f"QWidget#RightPanel {{ background-color: transparent; }} "
            f"QLineEdit {{ background-color: {in_bg}; border: 1px solid #3a2e4c; border-radius: 4px; color: #ffffff; padding: 7px; font-family: '{self.current_font}'; }} "
            f"QLineEdit:focus {{ border: 1px solid {self.current_accent_color}; }} "
            f"QListWidget {{ background-color: {ls_bg}; border: 1px solid #3a2e4c; border-radius: 4px; color: #ffffff; font-family: '{self.current_font}'; }} "
            f"QListWidget::item {{ padding: 6px; border-bottom: 1px solid #231a33; }} "
            f"QListWidget::item:hover {{ background-color: rgba(138, 43, 226, 40); color: {self.current_accent_color}; }} "
            f"QListWidget::item:selected {{ background-color: {self.current_accent_color}; color: #ffffff; }} "
            f"QSlider#VolumeSlider::groove:horizontal {{ height: 6px; background-color: #3a2e4c; border-radius: 3px; }} "
            f"QSlider#VolumeSlider::sub-page:horizontal {{ background-color: {self.current_accent_color}; border-radius: 3px; }} "
            f"QSlider#VolumeSlider::handle:horizontal {{ background-color: {self.current_accent_color}; width: 14px; margin: -5px 0; border-radius: 7px; }} "
            f"QSlider#TimelineSlider::groove:horizontal {{ height: 4px; background-color: #2b223d; border-radius: 2px; }} "
            f"QSlider#TimelineSlider::sub-page:horizontal {{ background-color: {self.current_accent_color}; border-radius: 2px; }} "
            f"QSlider#TimelineSlider::handle:horizontal {{ background-color: #ffffff; border: 2px solid {self.current_accent_color}; width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}"
        )
        self.title_lbl.setStyleSheet(f"color: {self.current_accent_color}; font-size: 24px; font-weight: bold; letter-spacing: 3px; background: transparent; border: none; font-family: '{self.current_font}';")
        self.playlist_lbl.setStyleSheet(f"color: #cccccc; font-size: 13px; font-weight: bold; background: transparent; border: none; font-family: '{self.current_font}';")
        self.status_lbl.setStyleSheet(f"color: #ffffff; font-weight: bold; background-color: rgba(15, 10, 20, 220); padding: 6px; border-radius: 4px; border: none; font-family: '{self.current_font}';")
        # Стили панели «Now Playing»
        self.cover_label.setStyleSheet(f"background-color: {in_bg}; border: 2px solid {self.current_accent_color}; border-radius: 12px; color: {self.current_accent_color}; font-size: 56px; font-family: '{self.current_font}';")
        # Блок с названием и автором: рамки нет вообще — и своего, и унаследованной.
        # Раньше QLabel подхватывал белую обводку, и фиолетовый фон выглядел «в рамке».
        self.author_label.setStyleSheet(f"color: #bfaee0; font-size: 17px; font-weight: 600; letter-spacing: 1px; background: transparent; border: none; border-radius: 0px; outline: none; padding: 0px; font-family: '{self.current_font}';")
        self.track_label.setStyleSheet(f"color: #ffffff; font-size: 22px; font-weight: bold; background: transparent; border: none; border-radius: 0px; outline: none; padding: 0px; font-family: '{self.current_font}';")
        self.volume_lbl.setStyleSheet(f"color: {self.current_accent_color}; font-size: 18px; background: transparent; border: none; font-family: '{self.current_font}';")
        self.volume_val_lbl.setStyleSheet(f"color: #cccccc; font-size: 12px; background: transparent; border: none; font-family: '{self.current_font}';")
        if hasattr(self, "time_cur_lbl"):
            self.time_cur_lbl.setStyleSheet(f"color: #bfaee0; font-size: 12px; font-weight: bold; background: transparent; border: none; font-family: '{self.current_font}';")
            self.time_total_lbl.setStyleSheet(f"color: #8878a5; font-size: 12px; font-weight: bold; background: transparent; border: none; font-family: '{self.current_font}';")
        self.lyrics_label.set_theme(self.current_lyrics_color, self.current_font)
        self.waveform.set_accent(self.current_accent_color)
        # Пересобираем перенос строк, если шрифт изменился уже на готовом окне
        if getattr(self, '_raw_track_title', None) and self.track_label.width() > 50:
            self.track_label.setText(self._fit_lines(self.track_label, self._raw_track_title, 1))
        if getattr(self, '_raw_author', None) and self.author_label.width() > 50:
            self.author_label.setText(self._fit_lines(self.author_label, self._raw_author, 2))
        for b in self.findChildren(QPushButton):
            b.setStyleSheet(self._button_style_for(b))
        if hasattr(self, 'settings_win') and self.settings_win.isVisible(): self.settings_win.update_theme()

    def save_genius_token(self):
        """Сохраняем токен Genius: с ним поиск идёт по его базе, где тексты точнее."""
        box = getattr(self, "settings_win", None)
        token = box.genius_edit.text().strip() if box is not None else ""
        if not token:
            self.status_lbl.setText("Статус: Токен Genius не введён — Genius не используется")
            return
        if lyrics_web.save_token(token):
            self.status_lbl.setText("Статус: Токен Genius сохранён — Genius включён в поиск")
        else:
            self.status_lbl.setText("Статус: Не удалось сохранить токен Genius")

    def open_settings(self):
        self.settings_win = SettingsWindow(self)
        self.settings_win.show()

    def fetch_vk_token_auto(self):
        """Кнопка «Получить токен автоматически»: вход по логину и паролю.

        Пароль передаётся в поток и сразу стирается из поля: он не должен лежать
        в интерфейсе, пока идёт запрос. В файлы он не пишется никогда.
        """
        box = getattr(self, "settings_win", None)
        if box is None:
            return
        if getattr(self, "_vk_auto_cooldown", False):
            # ВК ограничил вход: до конца паузы кнопку не дёргаем —
            # каждая новая попытка продлевала бы блокировку.
            self.status_lbl.setText(
                "Статус: ВК ограничил вход — подождите паузу, не нажимайте кнопку")
            return
        login = box.vk_login_edit.text().strip()
        password = box.vk_password_edit.text()
        if not login or not password:
            self.status_lbl.setText("Статус: Введите логин и пароль от ВК")
            return
        previous = getattr(self, "_vk_auto_thread", None)
        if previous is not None and previous.isRunning():
            self.status_lbl.setText("Статус: Вход в ВК уже идёт, подождите…")
            return
        code = box.vk_code_edit.text().strip() or "GET_CODE"
        # Сразу чистим поле: пароль больше не нужен в интерфейсе
        box.vk_password_edit.clear()
        box.btn_vk_auto.setEnabled(False)
        self.status_lbl.setText("Статус: Входим в ВК и получаем токен…")
        worker = VkAudioTokenWorker(login, password, code, parent=self)
        worker.fetched.connect(self._on_vk_token_fetched)
        worker.need_code.connect(self._on_vk_need_code)
        worker.failed.connect(self._on_vk_token_failed)
        self._vk_auto_thread = worker
        worker.finished.connect(lambda w=worker: self._clear_vk_auto(w))
        worker.start()

    def _clear_vk_auto(self, worker):
        if getattr(self, "_vk_auto_thread", None) is worker:
            self._vk_auto_thread = None
        box = getattr(self, "settings_win", None)
        if box is None:
            return
        try:
            # Во время блокировки кнопку не возвращаем — её разблокирует
            # таймер паузы, иначе пользователь снова нажмёт и продлит бан.
            box.btn_vk_auto.setEnabled(not getattr(self, "_vk_auto_cooldown", False))
        except RuntimeError:
            pass                          # окно настроек уже успели закрыть

    def _on_vk_token_fetched(self, token, user_agent, message):
        """Токен получен: сохраняем и сразу проверяем, что он рабочий."""
        if not token:
            self.status_lbl.setText("Статус: ВК вернул пустой токен")
            return
        save_vk_token(token)
        save_vk_user_agent(user_agent)
        self.vk_token = token
        global VK_ACCESS_TOKEN
        VK_ACCESS_TOKEN = token
        box = getattr(self, "settings_win", None)
        if box is not None:
            box.vk_token_edit.setText(token)
        self.status_lbl.setText(f"Статус: {message} — токен сохранён")

    def _on_vk_need_code(self, message):
        """ВК требует код из СМС — просим его и просим нажать кнопку ещё раз."""
        box = getattr(self, "settings_win", None)
        if box is not None:
            box.vk_code_edit.setFocus()
            box.vk_code_edit.setPlaceholderText("введите код из СМС и нажмите кнопку выше")
        self.status_lbl.setText("Статус: " + message)

    def _on_vk_token_failed(self, reason):
        self.status_lbl.setText("Статус: " + vk_error_ru(reason))
        if vk_error_is_flood(reason):
            # Пауза 10 минут: пока длится блокировка ВК, каждое нажатие
            # продлевает её. Гасим кнопку, чтобы случайно не дёрнуть.
            self._vk_auto_cooldown = True
            box = getattr(self, "settings_win", None)
            if box is not None:
                try:
                    box.btn_vk_auto.setEnabled(False)
                    box.btn_vk_auto.setText("⏳ ВК ограничил вход — подождите")
                except RuntimeError:
                    return
            QTimer.singleShot(10 * 60 * 1000, self._release_vk_auto)

    def _release_vk_auto(self):
        """Конец паузы после блокировки ВК: возвращаем кнопку как прежде."""
        self._vk_auto_cooldown = False
        box = getattr(self, "settings_win", None)
        if box is None:
            return
        try:
            box.btn_vk_auto.setEnabled(True)
            box.btn_vk_auto.setText("🔐 Получить токен автоматически")
        except RuntimeError:
            pass                          # окно настроек уже успели закрыть

    def check_vk_token(self):
        """Кнопка «Проверить токен»: живой запрос к ВК, чтобы не гадать."""
        box = getattr(self, "settings_win", None)
        token = (box.vk_token_edit.text().strip() if box is not None
                 else getattr(self, "vk_token", ""))
        if not token:
            self.status_lbl.setText("Статус: Сначала вставьте токен в поле выше")
            return
        previous = getattr(self, "_vk_check_thread", None)
        if previous is not None and previous.isRunning():
            return                      # проверка уже идёт
        self.status_lbl.setText("Статус: Проверяем токен в ВК...")
        worker = VkTokenCheckWorker(token, parent=self)
        worker.checked.connect(self._on_vk_token_checked)
        self._vk_check_thread = worker
        worker.finished.connect(lambda w=worker: self._clear_vk_check(w))
        worker.start()

    def _clear_vk_check(self, worker):
        if getattr(self, "_vk_check_thread", None) is worker:
            self._vk_check_thread = None

    def _on_vk_token_checked(self, ok, message):
        """Показываем результат проверки и, если токен рабочий, сохраняем его."""
        self.status_lbl.setText("Статус: " + message)
        if ok:
            box = getattr(self, "settings_win", None)
            if box is not None and box.vk_token_edit.text().strip() != self.vk_token:
                if save_vk_token(box.vk_token_edit.text().strip()):
                    self.vk_token = box.vk_token_edit.text().strip()

    def open_vk_apps(self):
        """Открывает страницу создания приложений ВК прямо из настроек."""
        url = "https://vk.com/apps?act=manage"
        try:
            opened = QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:
            console(f"Не удалось открыть браузер: {exc}")
            opened = False
        self.status_lbl.setText(
            "Статус: страница приложений ВК — " + (url if opened else "не открылась, скопируйте адрес вручную"))

    def save_vk_token(self):
        """Сохраняем токен ВК из окна настроек — с ним ищет VK Музыка."""
        box = getattr(self, "settings_win", None)
        token = box.vk_token_edit.text().strip() if box is not None else ""
        if not token:
            self.vk_token = ""
            if save_vk_token(""):
                self.vk_token = load_vk_token()
            self.status_lbl.setText("Статус: Токен ВК убран — поиск в VK Музыке выключен")
            return
        if not save_vk_token(token):
            self.status_lbl.setText("Статус: Не удалось сохранить токен ВК")
            return
        self.vk_token = token
        # Токен должен увидеть и уже созданный поисковый поток
        global VK_ACCESS_TOKEN
        VK_ACCESS_TOKEN = token
        self.status_lbl.setText("Статус: Токен ВК сохранён — доступен поиск в VK Музыке")

    def change_opacity(self, val):
        self.current_opacity = val / 100.0
        self.apply_custom_theme()

    def choose_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.current_accent_color = color.name()
            self.apply_custom_theme()
            self.status_lbl.setText(" Цвет всех кнопок изменен")

    def toggle_blur(self):
        if not self.blur_enabled:
            blur = QGraphicsBlurEffect(); blur.setBlurRadius(12)
            self.bg_label.setGraphicsEffect(blur); self.blur_enabled = True
            self.status_lbl.setText(" Размытие включено")
        else:
            self.bg_label.setGraphicsEffect(None); self.blur_enabled = False
            self.status_lbl.setText(" Размытие выключено")

    def change_font_style(self):
        self.current_font_index = (self.current_font_index + 1) % len(self.font_styles)
        self.current_font = self.font_styles[self.current_font_index]
        self.apply_custom_theme()
        self.status_lbl.setText(f"Статус: Установлен шрифт {self.current_font}")

    def choose_image(self):
        p, _ = QFileDialog.getOpenFileName(self, "Выберите фото", "", "Images (*.png *.jpg *.jpeg)")
        if p:
            self.bg_label.setMovie(None); self.bg_label.setPixmap(QPixmap(p))
            self.status_lbl.setText("Фото установлено")

    def choose_gif(self):
        p, _ = QFileDialog.getOpenFileName(self, "надо GIF", "", "GIF Files (*.gif)")
        if p:
            self.bg_label.setPixmap(QPixmap()); m = QMovie(p)
            self.bg_label.setMovie(m); m.start()
            self.status_lbl.setText("Гифка установлена")

    def clean_sc_url(self, url):
        """Оставляет чистую ссылку на трек: отрезает ?si=..., &utm_source=... и #хвост.

        Имя метода оставлено прежним: прежний код и тесты вызывают именно его.

        Исключение — ссылки ВК: там в query прячется сам id трека
        (?z=audio-123_456), и отрезать его нельзя — ссылка станет заглушкой.
        """
        if not url:
            return ""
        raw = url.strip()
        if detect_platform(raw) == "vk":
            return raw.split('#')[0].rstrip('/')
        clean = raw.split('#')[0].split('?')[0].rstrip('/')
        return clean

    def clean_url(self, url):
        """То же, что clean_sc_url: убирает трекинговые параметры из любой ссылки.

        Работает для всех платформ, а не только SoundCloud.
        """
        return self.clean_sc_url(url)


    def clean_title(self, url):
        """Запасное название из ссылки — без перевода и смены регистра."""
        try:
            slug = self.clean_sc_url(url).split('/')[-1].replace('-', ' ')
            return slug or "Трек SoundCloud"
        except Exception:
            return "Трек SoundCloud"

    def add_to_playlist(self):
        url = self.clean_url(self.url_input.text())
        if not url:
            # В поле не ссылка, а название — значит это поиск, а не добавление
            self.search_soundcloud()
            return
        if self.add_search_result(self._track_from_url(url)):
            self.url_input.clear()

    def _track_from_url(self, url):
        """Готовит трек из ссылки: определяем платформу и подсказываем название.

        Ссылки на SoundCloud, YouTube, ВК, Яндекс.Музыку и Spotify попадают в
        плейлист одинаково — с меткой платформы, которая видна в списке.
        Для Spotify и ВК пытаемся сразу взять настоящее название и обложку.
        """
        platform = detect_platform(url) or "sc"
        known = platform_name(platform)
        track = {"type": platform, "platform": platform,
                 "url": url, "title": self.clean_title(url)}
        # Spotify отдаёт название и обложку без всякой авторизации — берём их
        if platform == "spotify":
            info = spotify_track_info(url)
            if info:
                track.update({k: v for k, v in info.items() if v})
        if platform == "vk" and vk_audio_ids_from_url(url):
            # Аудиоссылку ВК превращаем в настоящий трек: без этого добавилась
            # бы заглушка «audio», которая к тому же не играет (yt-dlp ВК-аудио
            # не берёт). Тянем название, автора и прямую ссылку на mp3.
            # Не-аудио ссылки (видео, записи) добавляем как обычные.
            info = vk_audio_from_link(url)
            if not info:
                # Аудио есть в ссылке, но ВК не отдал трек: обычно нет токена
                # или ссылка битая — добавлять пустышку бессмысленно.
                self.status_lbl.setText(
                    "Статус: ВК — не удалось получить трек по ссылке "
                    "(нужен токен в настройках или проверьте ссылку)")
                return None
            track.update({k: v for k, v in info.items() if v})
        limit = PLATFORM_LIMITS.get(platform)
        if limit:
            self.status_lbl.setText(f"Статус: {known} — {limit}")
        else:
            self.status_lbl.setText(f"Статус: Добавляем трек с {known or 'платформы'}")
        return track


    def add_search_result(self, track):
        """Добавляет трек (из поиска, по ссылке или с ПК) и сохраняет плейлист."""
        if not isinstance(track, dict):
            # Ссылка не разобралась (например, ВК не ответил) — не роняем
            # вызывающий код и не добавляем мусор.
            return False
        platform = track.get("platform") or track.get("type") or ""
        limit = PLATFORM_LIMITS.get(platform)
        if not self._add_track(track):
            return False
        index = len(self.playlist_tracks) - 1
        title = self.playlist_tracks[index].get("title") or ""
        url = self.playlist_tracks[index].get("url", "")
        if limit:
            # Предупреждение о платформе важнее сообщения «добавлено»: иначе
            # пользователь не поймёт, почему трек не играет.
            self.status_lbl.setText(
                f"Статус: добавлено, но {platform_name(platform)} — {limit}")
            return True
        self.status_lbl.setText(f"Статус: Добавлено в плейлист — {title}")
        if url and not self._cached_file(url):
            # Качаем в фоне, чтобы трек из плейлиста стартовал без задержки
            self.status_lbl.setText(
                "Статус: Трек добавлен — качаю в кэш для мгновенного запуска…")
            threading.Thread(target=self._prefetch_track, args=(url,), daemon=True).start()
        return True

    def play_track_by_url(self, url):
        """Включить трек по ссылке (используется окном поиска)."""
        clean = self.clean_sc_url(url)
        if clean:
            self.download_and_play(clean)

    def search_soundcloud(self):
        """Ищем трек по названию сразу на всех доступных площадках.

        Название метода оставлено прежним, чтобы не ломать уже настроенные
        подключения; сам поиск давно не только SoundCloud.
        """

        query = self.url_input.text().strip()
        if not query:
            self.status_lbl.setText("Статус: Введите название трека для поиска")
            return
        if self._search_thread is not None and self._search_thread.isRunning():
            self.status_lbl.setText("Статус: Поиск уже идёт, подождите…")
            return
        names = " + ".join(platform_name(p) for p in SEARCH_ALL_PLATFORMS)
        self.status_lbl.setText(f"Статус: Ищу «{query}» — {names}…")
        worker = SearchWorker(query, parent=self)
        worker.results.connect(self._on_search_results)
        worker.failed.connect(self._on_search_failed)
        # Показываем, какая площадка ответила, а какая ошиблась: пользователь
        # видит, что ВК или Spotify просто недоступны, а не «сломался плеер».
        worker.progress.connect(self._on_search_progress)
        worker.platform_failed.connect(self._on_search_platform_failed)
        self._search_thread = worker
        worker.finished.connect(lambda w=worker: self._clear_search_thread(w))
        worker.start()

    def _clear_search_thread(self, worker):
        if self._search_thread is worker:
            self._search_thread = None

    def _poll_search(self):
        """Тик сторожевого таймера поиска — живёт в главном потоке Qt."""
        worker = self._search_thread
        if worker is not None and hasattr(worker, "poll"):
            worker.poll()

    def _on_search_progress(self, platform, done, total):
        """Отчиталась площадка: показываем прогресс по платформам."""
        self.status_lbl.setText(
            f"Статус: Поиск «{self.url_input.text().strip()}» — "
            f"{platform}: {done} из {total}…")

    def _on_search_platform_failed(self, platform, reason):
        """Площадка не ответила. Пишем в консоль, статус перебивает общий итог."""
        console(f"Поиск {platform}: {reason}")

    def _on_search_results(self, tracks):
        """Показываем найденные треки в отдельном окне."""
        if not tracks:
            self.status_lbl.setText("Статус: Ничего не найдено")
            return
        self.search_window = SearchWindow(self, self.url_input.text().strip(), tracks)
        self.search_window.show()
        # Убираем пустые подписи ДО сортировки: sorted() возвращает список,
        # а «список минус множество» — TypeError, который роняет всё окно.
        found = ", ".join(sorted({t.get("platform_name", "")
                                  or platform_name(t.get("platform") or t.get("type"))
                                  for t in tracks} - {""}))
        tail = f" ({found})" if found else ""
        self.status_lbl.setText(f"Статус: Найдено треков — {len(tracks)}{tail}")

    def _on_search_failed(self, reason):
        self.status_lbl.setText("Статус: Поиск не удался — проверьте интернет")
        console(f"Поиск: {reason}")

    def play_now_from_input(self):
        url = self.clean_sc_url(self.url_input.text())
        if url:
            self.download_and_play(url)
        else:
            # Не ссылка — значит ищем по всем площадкам сразу
            self.search_soundcloud()

    def play_selected_from_playlist(self, item):
        self._open_track_at(self.playlist_widget.row(item))

    def _playlist_path(self):
        """Файл плейлиста рядом с плеером — список переживает перезапуск."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), PLAYLIST_FILE)

    def save_playlist_clicked(self):
        """Кнопка «Сохранить список» — на случай, если нужно сохранить вручную."""
        if self.save_playlist():
            self.status_lbl.setText(
                f"Статус: Плейлист «{self.active_playlist_name}» сохранён — "
                f"треков: {len(self.playlist_tracks)}")
        else:
            self.status_lbl.setText("Статус: Не удалось сохранить плейлист")

    def save_playlist(self):
        """Сохраняем ВСЕ плейлисты: активный плюс остальные списки.

        Формат playlist.json: {"version": 2, "active": имя, "playlists": {...}}.
        Поле "tracks" дублирует активный список — для старых версий и тестов.
        """
        if not getattr(self, "playlists", None):
            self.playlists = {DEFAULT_PLAYLIST_NAME: []}
        if self.active_playlist_name not in self.playlists:
            self.active_playlist_name = next(iter(self.playlists))
        self.playlists[self.active_playlist_name] = self.playlist_tracks
        data = {"version": 2, "active": self.active_playlist_name,
                "playlists": self.playlists, "tracks": self.playlist_tracks}
        try:
            with open(self._playlist_path(), "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            return True
        except OSError as exc:
            console(f"Плейлист не сохранён: {exc}")
            return False

    def load_playlist(self):
        """Читаем playlist.json: все списки, активный показываем на экране."""
        store, active = load_playlist_store(self._playlist_path())
        self.playlists = store
        self.active_playlist_name = active
        self.playlist_tracks = self.playlists[self.active_playlist_name]
        pending = list(self.playlist_tracks)
        self.playlists[self.active_playlist_name] = []
        self.playlist_tracks = self.playlists[self.active_playlist_name]
        if hasattr(self, "playlist_widget"):
            self.playlist_widget.clear()
        restored = 0
        for track in pending:
            if self._add_track(track, save=False):
                restored += 1
        self._refresh_playlist_box()
        if restored:
            self.status_lbl.setText(
                f"Статус: Плейлист «{self.active_playlist_name}» "
                f"восстановлен — треков: {restored}")
        return restored

    # --- Несколько плейлистов: выбор и переключение ------------------------
    def _refresh_playlist_box(self):
        """Список имён в комбобоксе = ключи словаря плейлистов."""
        box = getattr(self, "playlist_box", None)
        if box is None:
            return
        self._playlist_loading = True
        try:
            box.blockSignals(True)
            box.clear()
            for name in self.playlists:
                box.addItem(f"{name} ({len(self.playlists[name])})", name)
            index = box.findData(self.active_playlist_name)
            box.setCurrentIndex(index if index >= 0 else 0)
        finally:
            box.blockSignals(False)
            self._playlist_loading = False

    def _show_playlist_tracks(self):
        """Перерисовать виджет треками активного плейлиста."""
        self.playlist_widget.clear()
        self.current_index = None
        self.current_url = ""
        for track in self.playlist_tracks:
            self.playlist_widget.addItem(track.get("title") or "?")
            self._update_playlist_item(self.playlist_widget.count() - 1)

    def switch_playlist(self, name):
        """Переключиться на плейлист name. Возвращает True при успехе."""
        clean = normalize_playlist_name(name)
        if clean not in self.playlists:
            return False
        if clean == self.active_playlist_name:
            return True
        self.playlists[self.active_playlist_name] = self.playlist_tracks
        self.active_playlist_name = clean
        self.playlist_tracks = self.playlists[clean]
        self._show_playlist_tracks()
        self._refresh_playlist_box()
        self.save_playlist()
        self.status_lbl.setText(
            f"Статус: Плейлист «{clean}» — треков: {len(self.playlist_tracks)}")
        return True

    def _on_playlist_chosen(self, _text=""):
        """Выбор в комбобоксе: имя берём из данных пункта, а не из текста."""
        if getattr(self, "_playlist_loading", False):
            return
        box = getattr(self, "playlist_box", None)
        if box is None:
            return
        name = box.currentData()
        if name:
            self.switch_playlist(name)

    def create_playlist(self, name):
        """Новый пустой плейлист и сразу переключиться на него."""
        clean = unique_playlist_name(name, self.playlists)
        self.playlists[self.active_playlist_name] = self.playlist_tracks
        self.playlists[clean] = []
        self.active_playlist_name = clean
        self.playlist_tracks = self.playlists[clean]
        self._show_playlist_tracks()
        self._refresh_playlist_box()
        self.save_playlist()
        self.status_lbl.setText(f"Статус: Новый плейлист «{clean}»")
        return clean

    def create_playlist_dialog(self):
        """Кнопка «➕»: спросить имя и создать плейлист."""
        suggested = unique_playlist_name("Новый плейлист", self.playlists)
        name, ok = QInputDialog.getText(self, "Новый плейлист",
                                       "Название:", text=suggested)
        if not ok:
            self.status_lbl.setText("Статус: Создание отменено")
            return ""
        return self.create_playlist(name)

    def delete_playlist_dialog(self):
        """Кнопка «🗑»: удалить текущий плейлист с подтверждением."""
        if len(self.playlists) <= 1:
            self.status_lbl.setText(
                "Статус: Последний плейлист удалить нельзя — он нужен для треков")
            return False
        name = self.active_playlist_name
        answer = QMessageBox.question(
            self, "Удалить плейлист",
            f"Удалить плейлист «{name}» ({len(self.playlist_tracks)} треков)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            self.status_lbl.setText("Статус: Удаление отменено")
            return False
        del self.playlists[name]
        self.active_playlist_name = next(iter(self.playlists))
        self.playlist_tracks = self.playlists[self.active_playlist_name]
        self._show_playlist_tracks()
        self._refresh_playlist_box()
        self.save_playlist()
        self.status_lbl.setText(
            f"Статус: Плейлист «{name}» удалён, открыт "
            f"«{self.active_playlist_name}»")
        return True

    def add_tracks_to_current(self, tracks):
        """Влить список треков в текущий плейлист. Возвращает число добавленных."""
        added = 0
        for track in tracks or []:
            if self._add_track(track, save=False):
                added += 1
        if added:
            self.save_playlist()
            self._refresh_playlist_box()
        return added

    def merge_playlist_from(self, src):
        """Влить все треки плейлиста src в текущий. Возвращает число добавленных."""
        if src not in self.playlists or src == self.active_playlist_name:
            return 0
        added = self.add_tracks_to_current(list(self.playlists[src]))
        self.status_lbl.setText(
            f"Статус: Из «{src}» в «{self.active_playlist_name}» "
            f"добавлено треков: {added}")
        return added

    def merge_playlist_dialog(self):
        """Кнопка «➕➕ Всё в текущий»: влить другой свой плейлист в текущий."""
        others = [n for n in self.playlists if n != self.active_playlist_name]
        if not others:
            self.status_lbl.setText(
                "Статус: Других плейлистов нет — сначала создайте или импортируйте")
            return 0
        src, ok = QInputDialog.getItem(
            self, "Добавить всё в текущий",
            f"Откуда добавить треки в «{self.active_playlist_name}»?",
            others, 0, False)
        if not ok or src not in self.playlists:
            self.status_lbl.setText("Статус: Слияние отменено")
            return 0
        return self.merge_playlist_from(src)

    def import_playlist_file(self, path, mode="new"):
        """Импорт без диалогов (для тестов): mode 'new' или 'current'."""
        imported = import_playlists_from_file(path)
        if not imported:
            return 0
        total = 0
        if mode == "current":
            for tracks in imported.values():
                total += self.add_tracks_to_current(tracks)
            self.status_lbl.setText(
                f"Статус: В «{self.active_playlist_name}» добавлено треков: {total}")
            return total
        self.playlists[self.active_playlist_name] = self.playlist_tracks
        for src, tracks in imported.items():
            clean = unique_playlist_name(src, self.playlists)
            self.playlists[clean] = []
            self.active_playlist_name = clean
            self.playlist_tracks = self.playlists[clean]
            self._show_playlist_tracks()
            total += self.add_tracks_to_current(tracks)
        self.status_lbl.setText(
            f"Статус: Импортировано плейлистов: {len(imported)} — треков: {total}")
        return total

    def import_playlist_dialog(self):
        """Кнопка «📥 Импорт»: файл -> как новый плейлист либо всё в текущий."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт плейлиста", "",
            "Плейлисты (*.json *.m3u *.m3u8 *.txt);;Все файлы (*.*)")
        if not path:
            self.status_lbl.setText("Статус: Импорт отменён")
            return 0
        imported = import_playlists_from_file(path)
        if not imported:
            self.status_lbl.setText(
                "Статус: В файле нет треков — импортировать нечего")
            return 0
        names = list(imported)
        if len(names) == 1:
            src = names[0]
            tracks = imported[src]
            choice, ok = QInputDialog.getItem(
                self, "Импорт плейлиста",
                f"В файле «{src}» треков: {len(tracks)}. Куда добавить?",
                ["Как новый плейлист",
                 f"Всё в текущий «{self.active_playlist_name}»"],
                0, False)
            if not ok:
                self.status_lbl.setText("Статус: Импорт отменён")
                return 0
            if choice.startswith("Как новый"):
                return self.import_playlist_file(path, mode="new")
            added = self.add_tracks_to_current(tracks)
            self.status_lbl.setText(
                f"Статус: В «{self.active_playlist_name}» добавлено треков: {added}")
            return added
        return self.import_playlist_file(path, mode="new")

    def export_playlist_dialog(self):
        """Кнопка «📤 Экспорт»: текущий плейлист в JSON или M3U."""
        if not self.playlist_tracks:
            self.status_lbl.setText("Статус: Плейлист пуст — экспортировать нечего")
            return False
        suggested = f"{self.active_playlist_name}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт плейлиста", suggested,
            "JSON (*.json);;M3U (*.m3u)")
        if not path:
            self.status_lbl.setText("Статус: Экспорт отменён")
            return False
        return self.export_playlist_to(path)

    def export_playlist_to(self, path):
        """Экспорт текущего плейлиста в файл (для кнопки и тестов)."""
        if not self.playlist_tracks:
            return False
        try:
            if str(path).lower().endswith((".m3u", ".m3u8")):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(playlist_to_m3u(self.playlist_tracks))
            else:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({"version": 1, "name": self.active_playlist_name,
                               "tracks": self.playlist_tracks},
                              fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            console(f"Экспорт не удался: {exc}")
            self.status_lbl.setText("Статус: Не удалось сохранить файл")
            return False
        self.status_lbl.setText(
            f"Статус: Плейлист «{self.active_playlist_name}» "
            f"экспортирован — треков: {len(self.playlist_tracks)}")
        return True

    def _add_track(self, track, save=True):
        """Один путь добавления трека: из ссылки, из поиска и с ПК.

        Возвращает True, если трек действительно добавлен.
        """
        if not isinstance(track, dict):
            return False
        kind = track.get("type") or ("local" if track.get("path") else "sc")
        url = (self.clean_url(track.get("url", ""))
               if kind != "local" else "")
        path = track.get("path", "") if kind == "local" else ""
        if kind == "local" and not path:
            return False
        if kind != "local" and not url:
            return False
        record = {"type": kind}
        if kind != "local":
            record["url"] = url
            record["title"] = track.get("title") or self.clean_title(url)
            # Платформу храним явно: по ней видно источник в списке и в плейлисте
            record["platform"] = (track.get("platform") or detect_platform(url) or kind)
            record["platform_name"] = platform_name(record["platform"])
        else:
            record["path"] = path
            record["title"] = track.get("title") or os.path.basename(path)

        if track.get("author"):
            record["author"] = track["author"]
        if track.get("cover"):
            record["cover"] = track["cover"]
        if track.get("duration"):
            record["duration"] = float(track["duration"])
        # Прямая ссылка на файл: по ней трек качается без yt-dlp (важно для ВК)
        if track.get("direct_url"):
            record["direct_url"] = track["direct_url"]
        if track.get("has_sync_lyrics"):
            record["has_sync_lyrics"] = True
        self.playlist_tracks.append(record)
        self.playlist_widget.addItem(record["title"])
        self._update_playlist_item(len(self.playlist_tracks) - 1)
        if save:
            self.save_playlist()
        return True

    def remove_track_at(self, index):
        """Удаляем трек из плейлиста и сразу сохраняем файл."""
        if index is None or not (0 <= index < len(self.playlist_tracks)):
            return False
        track = self.playlist_tracks.pop(index)
        item = self.playlist_widget.takeItem(index)
        del item
        # Если удалили играющий трек — музыка играет дальше, просто снимаем индекс,
        # чтобы автопереход не перепутал строки.
        if self.current_index == index:
            self.current_index = None
            self.current_url = ""
        elif self.current_index is not None and self.current_index > index:
            self.current_index -= 1
        self.save_playlist()
        name = track.get("title") or track.get("url") or track.get("path") or "трек"
        self.status_lbl.setText(f"Статус: Удалён из плейлиста — {name}")
        return True

    def remove_selected_track(self):
        """Кнопка «Удалить из плейлиста» под списком."""
        row = self.playlist_widget.currentRow()
        if row < 0:
            self.status_lbl.setText("Статус: Выберите трек в списке для удаления")
            return
        self.remove_track_at(row)

    def _show_playlist_menu(self, pos):
        """Правый клик по треку: воспроизвести или удалить из плейлиста."""
        item = self.playlist_widget.itemAt(pos)
        if item is None:
            return
        index = self.playlist_widget.row(item)
        menu = QMenu(self.playlist_widget)
        act_play = menu.addAction("▶ Воспроизвести")
        menu.addSeparator()
        act_del = menu.addAction("❌ Удалить трек из плейлиста")
        chosen = menu.exec(self.playlist_widget.mapToGlobal(pos))
        if chosen is act_play:
            self._open_track_at(index)
        elif chosen is act_del:
            self.remove_track_at(index)

    def _open_track_at(self, index):
        """Играет трек из плейлиста: ссылку SoundCloud или файл с ПК."""
        if index is None or not (0 <= index < len(self.playlist_tracks)):
            return False
        track = self.playlist_tracks[index]
        if (track.get("type") or "sc") == "local":
            path = track.get("path", "")
            if not path or not os.path.exists(path):
                self.status_lbl.setText("Статус: Файл с ПК не найден по сохранённому пути")
                return False
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
            return self._play_file(path, track.get("title") or os.path.basename(path),
                                   track.get("author") or "Локальный файл", index=index)
        return self._start_track(track.get("url", ""), index)

    def _fit_lines(self, label, text, max_lines):
        """Переносит текст по словам и ограничивает число строк с «…» на конце."""
        fm = label.fontMetrics()
        avail = max(80, label.width() or 170)
        lines, cur = [], ""
        for w in text.split():
            cand = (cur + " " + w).strip()
            if not cur or fm.horizontalAdvance(cand) <= avail:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            rest = " ".join(lines[max_lines - 1:])
            lines = lines[:max_lines - 1] + [fm.elidedText(rest, Qt.TextElideMode.ElideRight, avail)]
        return "\n".join(lines) if lines else text

    def set_now_playing(self, track_title, author="СвязьКлауд", cover_path=None):
        """Обновляет левую панель «Now Playing»: обложку, автора, название и титры."""
        if not track_title:
            track_title = "Неизвестный трек"
        self._raw_author = author
        self._raw_track_title = track_title
        self.author_label.setText(self._fit_lines(self.author_label, author, 2))
        self.track_label.setText(self._fit_lines(self.track_label, track_title, 1))
        if cover_path and os.path.exists(cover_path):
            pix = QPixmap(cover_path).scaled(
                self.cover_size, self.cover_size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self.cover_label.setText("")
            self.cover_label.setPixmap(pix)
        else:
            self.cover_label.setPixmap(QPixmap())
            self.cover_label.setText("♪")
        self.lyrics_label.set_scroll_text(f"Сейчас играет: {track_title}   •   {author}")
        # Локальное распознавание текста песни (Whisper, интернет не нужен)
        self._fetch_lyrics_async(getattr(self, "current_audio_file", "") or "", track_title)

    def _lyrics_cache_path(self, audio_path):
        """Текст лежит рядом с аудио: <трек>.txt — повторный запуск мгновенный."""
        if not audio_path:
            return ""
        return os.path.splitext(audio_path)[0] + ".txt"

    def _lrc_cache_path(self, audio_path):
        """Файл с таймкодами рядом с аудио: <трек>.lrc."""
        if not audio_path:
            return ""
        return os.path.splitext(audio_path)[0] + ".lrc"

    def _read_cached_lrc(self, audio_path):
        """Читает <трек>.lrc из кэша или с диска; без таймкодов вернёт ""."""
        if not audio_path:
            return ""
        known = self._lrc_memory.get(audio_path)
        if known:
            return known
        try:
            with open(self._lrc_cache_path(audio_path), "r", encoding="utf-8") as fh:
                text = fh.read().strip()
        except (OSError, ValueError):
            return ""
        if parse_lrc(text):
            self._lrc_memory[audio_path] = text
            return text
        return ""

    def _write_cached_lrc(self, audio_path, text):
        """Сохраняет таймкоды в <трек>.lrc — в следующий раз караоке пойдёт сразу."""
        if not audio_path or not parse_lrc(text):
            return
        self._lrc_memory[audio_path] = text
        try:
            with open(self._lrc_cache_path(audio_path), "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def apply_lrc(self, audio_path):
        """Включает караоке для трека: берём таймкоды из кэша или с диска."""
        text = self._read_cached_lrc(audio_path or "")
        if text and self.lyrics_label.set_lrc(text):
            return True
        return False

    def _on_whisper_lrc(self, track_title, audio_path, lrc_text):
        """Таймкоды от Whisper: сохраняем в .lrc и сразу включаем караоке."""
        if not self._whisper_title_matches(track_title):
            return
        self._write_cached_lrc(audio_path or "", lrc_text)
        if self.lyrics_label.set_lrc(lrc_text):
            self.status_lbl.setText("Статус: Текст песни идёт по ритму (Whisper)")

    def _read_cached_lyrics(self, audio_path):
        """Возвращает уже распознанный текст трека (или пустую строку)."""
        if not audio_path:
            return ""
        known = self._lyrics_memory.get(audio_path)
        if known:
            return known
        try:
            with open(self._lyrics_cache_path(audio_path), "r", encoding="utf-8") as fh:
                text = fh.read().strip()
        except (OSError, ValueError):
            return ""
        if text:
            self._lyrics_memory[audio_path] = text
        return text

    def _write_cached_lyrics(self, audio_path, text):
        """Сохраняет распознанный текст в <трек>.txt (если папка доступна на запись)."""
        if not audio_path or not text:
            return
        self._lyrics_memory[audio_path] = text
        try:
            with open(self._lyrics_cache_path(audio_path), "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def _start_whisper(self, audio_path, track_title):
        """Запасной путь: голос распознаём локально (интернет не помог или отключён)."""
        key = os.path.abspath(audio_path).lower()
        if (self._whisper_thread is not None and self._whisper_key == key
                and self._whisper_thread.isRunning()):
            return
        self._stop_whisper_thread()
        self._whisper_key = key
        self.status_lbl.setText("Статус: Whisper распознаёт текст песни…")
        worker = WhisperWorker(audio_path, track_title)
        worker.transcribed.connect(self._on_whisper_transcribed)
        worker.lrc.connect(self._on_whisper_lrc)
        worker.failed.connect(self._on_whisper_failed)

        worker.missing_dependency.connect(self._on_whisper_missing)
        worker.missing_ffmpeg.connect(self._on_whisper_missing_ffmpeg)
        worker._note.connect(self._on_whisper_note)
        self._whisper_thread = worker
        worker.finished.connect(lambda w=worker: self._clear_whisper_thread(w))
        worker.start()

    def _fetch_lyrics_async(self, audio_path, track_title):
        """Текст песни: кэш <трек>.txt -> поиск в интернете -> Whisper как запасной."""
        audio_path = audio_path or ""
        if not audio_path:
            return
        # Уже есть таймкоды (с диска или из прошлого распознавания) — показываем
        # караоке сразу, не запуская ни сеть, ни Whisper заново.
        if self.apply_lrc(audio_path):
            if self._whisper_title_matches(track_title):
                return
        # Уже показывали этот файл — сразу из памяти или кэша, без сети и CPU
        cached = self._read_cached_lyrics(audio_path)
        if cached:
            if self._whisper_title_matches(track_title):
                self.lyrics_label.set_scroll_text(cached)
            return
        # Тот же файл уже ищется или распознаётся — второй поток не нужен
        key = os.path.abspath(audio_path).lower()
        if (self._whisper_thread is not None and self._whisper_key == key
                and self._whisper_thread.isRunning()):
            return
        if (self._web_thread is not None and self._web_key == key
                and self._web_thread.isRunning()):
            return
        self._stop_web_thread()
        self._stop_whisper_thread()
        self._web_key = key
        self.status_lbl.setText("Статус: Ищем текст песни в интернете…")
        web = LyricsWebWorker(audio_path, track_title,
                              getattr(self, "_raw_author", "") or "")
        web.found.connect(self._on_lyrics_web_found)
        web.not_found.connect(self._on_lyrics_web_missing)
        self._web_thread = web
        web.finished.connect(lambda w=web: self._clear_web_thread(w))
        web.start()

    def _clear_web_thread(self, worker):
        """Поиск закончился — снимаем ссылку, чтобы состояние оставалось честным."""
        if self._web_thread is worker:
            self._web_thread = None
            self._web_key = ""

    def _stop_web_thread(self):
        """Трек сменили: результат поиска уже не нужен, не покажем чужой текст."""
        thread, self._web_thread = self._web_thread, None
        self._web_key = ""
        if thread is not None and thread.isRunning():
            thread.requestInterruption()

    def _on_lyrics_web_found(self, track_title, audio_path, text, source):
        """Текст нашли в интернете: показываем и кладём в кэш — Whisper не нужен."""
        if not self._whisper_title_matches(track_title):
            return                      # ответ по устаревшему треку игнорируем
        self._stop_whisper_thread()
        self._write_cached_lyrics(audio_path, text)
        self.lyrics_label.set_scroll_text(text)
        self.status_lbl.setText(f"Статус: Текст песни найден в интернете ({source})")

    def _on_lyrics_web_missing(self, track_title, audio_path):
        """В интернете не нашли — включаем локальное распознавание голоса."""
        if not self._whisper_title_matches(track_title):
            return
        self._start_whisper(audio_path, track_title)

    def _clear_whisper_thread(self, worker):
        """Поток досчитал — снимаем ссылку, чтобы состояние оставалось честным."""
        if self._whisper_thread is worker:
            self._whisper_thread = None
            self._whisper_key = ""

    def _stop_whisper_thread(self):
        """Отпускает прошлый поток: трек сменился, его результат уже не нужен."""
        thread, self._whisper_thread = self._whisper_thread, None
        self._whisper_key = ""
        if thread is not None and thread.isRunning():
            thread.requestInterruption()

    def _whisper_title_matches(self, track_title):
        """Ответ потока показываем, только если он про текущий трек."""
        return not track_title or track_title == getattr(self, "_raw_track_title", "")

    def _on_whisper_transcribed(self, track_title, audio_path, text):
        """Распознанный текст: сохраняем в <трек>.txt и показываем в блоке титров."""
        if not self._whisper_title_matches(track_title):
            # Результат устаревшего трека: ни кэша, ни титров — трек уже сменился
            return
        self._write_cached_lyrics(audio_path or "", text)
        self.lyrics_label.set_scroll_text(text)
        self.status_lbl.setText("Статус: Текст песни распознан локально (Whisper)")

    def _on_whisper_note(self, note):
        """Этап подготовки (разделение вокала и т.п.) показываем в статусе."""
        if self._whisper_thread is not None and note:
            self.status_lbl.setText("Статус: " + note)

    def _on_whisper_failed(self, track_title, reason):
        if self._whisper_title_matches(track_title):
            self.status_lbl.setText("Статус: Текст песни не распознан — трек продолжает играть")
        console(f"Whisper notice: {reason}")

    def _on_whisper_missing(self, track_title, reason=""):
        """Whisper не импортируется: говорим настоящую причину и предлагаем починить."""
        if not self._whisper_hint_shown:
            self._whisper_hint_shown = True
            console(WHISPER_INSTALL_HINT)
            if reason:
                console(f"Whisper import error: {reason}")
        if self._whisper_title_matches(track_title):
            self.lyrics_label.set_scroll_text(WHISPER_INSTALL_HINT)
            self.status_lbl.setText("Статус: В этом Python нет Whisper — "
                                    "запустите «Запустить плеер.cmd»")
            if "Program Files" in sys.executable:
                console("Внимание: плеер запущен из Program Files, туда нельзя "
                        "доустановить пакеты без прав администратора.")
                console("Запустите «Запустить плеер.cmd» — он выберет верный Python.")
        self._ask_install_whisper(reason)

    def _ask_install_whisper(self, reason=""):
        """Предлагаем поставить Whisper одной кнопкой: ручная команда отпугивает."""
        if self._whisper_asked or self._installer is not None:
            return
        self._whisper_asked = True
        box = QMessageBox(self)
        box.setWindowTitle("Текст песни")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Whisper не найден — текст песни не распознаётся.\n"
                    "Установить сейчас? Нужен интернет, около 500 МБ (torch + Whisper).")
        box.setInformativeText(f"Установка в этот Python:\n{sys.executable}"
                               + (f"\n\nПричина: {reason}" if reason else ""))
        install_btn = box.addButton("Установить", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Не сейчас", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(install_btn)
        box.finished.connect(lambda _code, b=box, i=install_btn:
                             self._start_whisper_install() if b.clickedButton() is i else None)
        box.open()               # без exec(): музыка продолжает играть
        self._install_box = box

    def _start_whisper_install(self):
        """Запускаем установку в фоне и сразу повторим трек после успеха."""
        self._installer = WhisperInstaller(parent=self)
        self._installer.log.connect(self._on_install_log)
        self._installer.done.connect(self._on_install_done)
        self.status_lbl.setText("Статус: Устанавливаем Whisper, это займёт несколько минут…")
        self._installer.start()

    def _on_install_log(self, line):
        if line:
            self.status_lbl.setText(f"Статус: Установка Whisper — {line[:70]}")

    def _on_install_done(self, ok, message):
        self._installer = None
        if not ok:
            self.status_lbl.setText("Статус: Не удалось установить Whisper")
            console(f"Whisper install failed: {message}")
            return
        console(f"Whisper: {message}")
        self.status_lbl.setText("Статус: Whisper установлен, распознаём текст песни…")
        # Повторяем для текущего трека: пользователю ничего делать не придётся
        self._fetch_lyrics_async(getattr(self, "current_audio_file", "") or "",
                                 getattr(self, "_raw_track_title", ""))

    def _on_whisper_missing_ffmpeg(self, track_title):
        """ffmpeg не найден — без него Whisper не читает mp3; подсказываем команду."""
        console(WHISPER_FFMPEG_HINT)
        if self._whisper_title_matches(track_title):
            self.lyrics_label.set_scroll_text(WHISPER_FFMPEG_HINT)
            self.status_lbl.setText("Статус: Whisper не может читать mp3 — нужен ffmpeg")

    def _compute_waveform(self, path):
        """Вычисляет реальную форму волны (40 амплитуд) из PCM данных аудиофайла."""
        try:
            snd = pygame.mixer.Sound(path)
            raw = snd.get_raw()
            if not raw or len(raw) < 1000:
                return []
            n = 40
            step = len(raw) // n
            amps = []
            for i in range(n):
                chunk = raw[i * step : i * step + 4000]
                if len(chunk) < 2:
                    amps.append(0.2)
                    continue
                vals = struct.unpack('<' + 'h' * (len(chunk) // 2), chunk)
                amps.append(max(abs(v) for v in vals) / 32768.0 if vals else 0.2)
            mx = max(amps) if amps else 1.0
            if mx > 0.05:
                amps = [round(v / mx, 3) for v in amps]
            return amps
        except Exception as e:
            print(f"Waveform calc notice: {e}")
            return []

    def play_local_file(self):
        """Воспроизведение аудиофайлов с ПК (mp3/wav/ogg/flac) через pygame."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть аудио файл с ПК", "",
            "Audio (*.mp3 *.wav *.ogg *.flac)")
        if not path:
            return
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        # Кладём файл в плейлист: путь сохранится в playlist.json и переживёт перезапуск
        self.add_search_result({"type": "local", "path": path,
                                "title": os.path.basename(path),
                                "author": "Локальный файл"})
        self._play_file(path, os.path.basename(path), author="Локальный файл")

    def _cache_dir(self):
        """Папка локального кэша: скачанные треки стартуют без задержки."""
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sc_cache")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        return folder

    def _cache_key(self, clean_url):
        return hashlib.sha1(clean_url.encode("utf-8")).hexdigest()

    def _is_audio_name(self, name):
        return name.rsplit(".", 1)[-1].lower() in (
            "mp3", "m4a", "webm", "opus", "ogg", "oga", "aac", "wav", "flac")

    def _audio_rank(self, path):
        """Сначала форматы, которые гарантированно читает pygame."""
        playable = ("mp3", "ogg", "oga", "wav", "flac", "opus")
        ext = path.rsplit(".", 1)[-1].lower()
        return (0, playable.index(ext)) if ext in playable else (1, 0)

    def _cached_file(self, clean_url):
        """Готовый файл из кэша или None (тогда трек запустится мгновенно)."""
        folder, prefix = self._cache_dir(), self._cache_key(clean_url) + "."
        try:
            found = [os.path.join(folder, name) for name in os.listdir(folder)
                     if name.startswith(prefix) and self._is_audio_name(name)]
        except OSError:
            return None
        if not found:
            return None
        found.sort(key=self._audio_rank)
        return found[0]

    def _cached_meta(self, clean_url):
        """Название и автор трека, сохранённые вместе с кэшем."""
        path = os.path.join(self._cache_dir(), self._cache_key(clean_url) + ".json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save_meta(self, clean_url, author, title):
        if not (author or title):
            return
        path = os.path.join(self._cache_dir(), self._cache_key(clean_url) + ".json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"author": author, "title": title}, fh, ensure_ascii=False)
        except OSError:
            pass

    def _cleanup_files(self, folder, prefix, keep=None):
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for name in names:
            path = os.path.join(folder, name)
            if name.startswith(prefix) and path != keep:
                try: os.remove(path)
                except OSError: pass

    def _ytdlp_command(self, clean_url, output_tpl):
        """Команда внешнего процесса yt-dlp: mp3, перезапись, метаданные, UA."""
        return [
            sys.executable, "-m", "yt_dlp",
            "--format", "bestaudio[ext=mp3]/bestaudio/best",
            "--force-overwrites",
            "--no-playlist",
            "--no-part",
            "--user-agent", "Mozilla/5.0",
            "--quiet", "--no-warnings",
            "--no-simulate",
            "--print", "%(uploader)s\t%(title)s\t%(thumbnail)s",
            "--output", output_tpl,
            clean_url,
        ]

    def _parse_metadata(self, stdout):
        """Из вывода yt-dlp берём автора, название и ссылку на обложку."""
        for line in reversed((stdout or "").splitlines()):
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t")]
                while len(parts) < 3:
                    parts.append("")
                author, title, thumb = parts[0], parts[1], parts[2]
                # yt-dlp подставляет "NA", если поле недоступно
                author = "" if author == "NA" else author
                title = "" if title == "NA" else title
                thumb = "" if thumb == "NA" else thumb
                return author, title, thumb
        return "", "", ""

    def _cached_cover(self, clean_url):
        """Путь к сохранённой обложке трека или пустая строка."""
        folder, prefix = self._cache_dir(), self._cache_key(clean_url) + "_cover."
        try:
            for name in os.listdir(folder):
                if name.startswith(prefix):
                    return os.path.join(folder, name)
        except OSError:
            pass
        return ""

    def _save_cover(self, clean_url, thumb_url):
        """Скачивает обложку трека в кэш (для панели Now Playing и списка)."""
        if not thumb_url:
            return ""
        ext = os.path.splitext(thumb_url.split("?")[0])[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            ext = ".jpg"
        folder = self._cache_dir()
        path = os.path.join(folder, self._cache_key(clean_url) + "_cover" + ext)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        try:
            import urllib.request
            req = urllib.request.Request(
                thumb_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as src, open(path, "wb") as dst:
                dst.write(src.read())
        except Exception as e:
            print(f"Не удалось загрузить обложку: {e}")
            return ""
        return path

    def fetch_to_cache(self, clean_url):
        """Качает трек и его обложку в кэш внешним процессом yt-dlp.

        Возвращает (путь_аудио, автор, название, путь_обложки).
        """
        ready = self._cached_file(clean_url)
        if ready:
            meta = self._cached_meta(clean_url)
            return (ready, meta.get("author", ""), meta.get("title", ""),
                    self._cached_cover(clean_url))
        folder = self._cache_dir()
        key = self._cache_key(clean_url)
        stamp = f"{key}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        output_tpl = os.path.join(folder, stamp + ".%(ext)s")
        proc = subprocess.run(
            self._ytdlp_command(clean_url, output_tpl),
            capture_output=True, text=False, env=_ytdlp_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            produced = [os.path.join(folder, name) for name in os.listdir(folder)
                        if name.startswith(stamp + ".") and self._is_audio_name(name)]
        except OSError:
            produced = []
        if proc.returncode != 0 or not produced:
            print(f"yt-dlp stderr: {_decode_ytdlp(proc.stderr).strip()}")
            self._cleanup_files(folder, stamp + ".")
            return None, "", "", ""
        produced.sort(key=self._audio_rank)
        ext = produced[0].rsplit(".", 1)[-1].lower()
        target = os.path.join(folder, key + "." + ext)
        try:
            os.replace(produced[0], target)
        except OSError:
            target = produced[0]
        self._cleanup_files(folder, stamp + ".", keep=target)
        author, title, thumb = self._parse_metadata(_decode_ytdlp(proc.stdout))
        self._save_meta(clean_url, author, title)
        cover = self._save_cover(clean_url, thumb)
        return target, author, title, cover

    def _prefetch_track(self, clean_url):
        """Фоновое скачивание: трек, автор и обложка готовы до нажатия."""
        try:
            # Прямая ссылка (ВК) — качаем ею: yt-dlp такие ссылки не берёт.
            direct = self._direct_url_for(clean_url)
            if direct:
                path = self._download_direct(direct, clean_url)
                if path:
                    track = self._track_by_index(None, clean_url) or {}
                    cover = self._save_cover(clean_url, track.get("cover", ""))
                    self._ui_queue.put(("cached", clean_url,
                                        track.get("author", ""),
                                        track.get("title", ""), cover))
                    return
                # Прямая ссылка не сработала — для ВК лучше не мучить yt-dlp,
                # трек доберётся при самом запуске.
                if detect_platform(clean_url) == "vk":
                    return
            path, author, title, cover = self.fetch_to_cache(clean_url)
        except Exception as e:
            print(f"Техническая ошибка кэша: {e}")
            return
        if path:
            self._ui_queue.put(("cached", clean_url, author, title, cover))

    def _drain_ui_queue(self):
        """Обновляет интерфейс сообщениями фоновых потоков (в потоке Qt)."""
        while True:
            try:
                message = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            if message and message[0] == "cached":
                _, clean_url, author, title, cover = message
                self._apply_track_meta(clean_url, author, title, cover)
                if not self.is_playing:
                    self.status_lbl.setText("Кэш готов: трек запустится без задержки")

    def _apply_track_meta(self, clean_url, author="", title="", cover=""):
        """Проставляет автора и обложку в плейлисте и в панели Now Playing."""
        for index, track in enumerate(self.playlist_tracks):
            if track.get("url") == clean_url:      # у локальных файлов url нет
                self._update_playlist_item(index, title=title, author=author, cover=cover)
        if clean_url and clean_url == self.current_url and (author or title or cover):
            self.set_now_playing(title or self.clean_title(clean_url),
                                 author=author or "SoundCloud", cover_path=cover)

    def _update_playlist_item(self, index, title="", author="", cover=""):
        """Строка плейлиста: обложка + «Автор — Название»."""
        item = self.playlist_widget.item(index)
        if item is None or index >= len(self.playlist_tracks):
            return
        track = self.playlist_tracks[index]
        if title:
            track["title"] = title
        if author:
            track["author"] = author
        if cover:
            track["cover"] = cover
        label = track.get("title", "")
        if track.get("author"):
            label = f"{track['author']} — {track['title']}"
        # Метка платформы в скобках: «Автор — Название (SoundCloud)»
        source = (track.get("platform_name") or platform_name(track.get("platform"))
                  or platform_name(track.get("type")))
        if source:
            label = f"{label} ({source})"
        item.setText(label)

        if (track.get("type") or "sc") == "local":
            cover_path = ""                    # у файла с ПК обложки в кэше нет
        else:
            cover_path = track.get("cover") or self._cached_cover(track.get("url", ""))
        if cover_path and os.path.exists(cover_path):
            pix = QPixmap(cover_path)
            if not pix.isNull():
                item.setIcon(QIcon(pix.scaled(
                    40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)))

    def _start_track(self, clean_url, index=None):
        """Запускает трек: из кэша мгновенно либо скачав его внешним yt-dlp."""
        if not clean_url:
            return False
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass

        ready = self._cached_file(clean_url)
        if ready:
            meta = self._cached_meta(clean_url)
            title = meta.get("title") or self.clean_title(clean_url)
            self._play_file(ready, title, meta.get("author"),
                            self._cached_cover(clean_url), clean_url, index,
                            from_cache=True)
            return True

        self.status_lbl.setText("Статус: Буферизация СвязьКлауд...")
        QApplication.processEvents()
        # Площадки, отдающие прямую ссылку на файл (ВК), качаем ею: yt-dlp такие
        # ссылки не понимает, а обычный запрос работает.
        direct = self._direct_url_for(clean_url, index)
        if direct:
            self.status_lbl.setText("Статус: Качаем трек напрямую с площадки...")
            QApplication.processEvents()
            path = self._download_direct(direct, clean_url, index)
            if path:
                track = self._track_by_index(index, clean_url) or {}
                cover = self._save_cover(clean_url, track.get("cover", ""))
                if self._play_file(path, track.get("title") or self.clean_title(clean_url),
                                   track.get("author") or "", cover, clean_url, index):
                    return True
                return False
            # Прямая ссылка не сработала — открываем трек в браузере
            self._listen_in_browser(clean_url, index)
            return False
        try:
            audio_path, author, title, cover = self.fetch_to_cache(clean_url)
        except Exception as e:
            self.status_lbl.setText("Статус: Не удалось запустить yt-dlp")
            print(f"Техническая ошибка: {e}")
            return False
        if not audio_path:
            self.status_lbl.setText("Статус: Ошибка сети или заблокированный трек")
            return False
        self._play_file(audio_path, title or self.clean_title(clean_url), author,
                        cover, clean_url, index)
        return True

    def download_and_play(self, url, index=None):
        """Точка входа: играть трек по ссылке (index — для автоперехода)."""
        self._start_track(self.clean_sc_url(url), index)

    # --- Проигрывание треков с прямой ссылкой на файл (ВК и подобные) ---------
    def _track_by_index(self, index, clean_url=""):
        """Находит запись трека в плейлисте по индексу или по ссылке."""
        if index is not None and 0 <= index < len(self.playlist_tracks):
            return self.playlist_tracks[index]
        for track in self.playlist_tracks:
            if clean_url and track.get("url") == clean_url:
                return track
        return None

    def _direct_url_for(self, clean_url, index=None):
        """Прямая ссылка на аудиофайл трека или "".

        У ссылки из ВК она протухает через несколько часов, поэтому, если
        сохранённая ссылка не сработала, достаём её заново из VK API по id
        из ссылки — трек играет, даже если запись добавили давно.
        """
        track = self._track_by_index(index, clean_url) or {}
        direct = track.get("direct_url") or ""
        if not direct and detect_platform(clean_url) == "vk":
            info = vk_audio_from_link(clean_url) or {}
            direct = info.get("direct_url") or ""
            if direct and track:
                # Записываем в плейлист, чтобы в следующий раз не ходить в ВК
                track["direct_url"] = direct
                for key in ("title", "author", "cover", "duration"):
                    if info.get(key) and not track.get(key):
                        track[key] = info[key]
        return direct

    def _download_direct(self, direct_url, clean_url, index=None):
        """Качает аудиофайл по прямой ссылке в кэш плеера."""
        key = self._cache_key(clean_url)
        target = os.path.join(self._cache_dir(), key)
        path = download_audio(direct_url, target)
        if not path:
            return ""
        track = self._track_by_index(index, clean_url) or {}
        self._save_meta(clean_url, track.get("author", ""), track.get("title", ""))
        return path

    def _listen_in_browser(self, clean_url, index=None):
        """Запасной вариант: открыть трек в браузере, где он точно зазвучит.

        Прямая ссылка могла протухнуть или площадка могла не отдать файл —
        тогда не оставляем пользователя без музыки, а отправляем его слушать
        через их собственный плеер.
        """
        track = self._track_by_index(index, clean_url) or {}
        title = track.get("title") or "трек"
        QMessageBox.information(
            self, "Слушать в браузере",
            f"Не удалось скачать «{title}».\n\n"
            "Прямая ссылка на файл устаревает через несколько часов — обычно "
            "помогает найти трек заново в поиске.\n\n"
            "Сейчас откроем трек в браузере: там он зазвучит через плеер ВК.")
        try:
            from PyQt6.QtGui import QDesktopServices
            opened = QDesktopServices.openUrl(QUrl(clean_url))
        except Exception as exc:
            console(f"Не удалось открыть браузер: {exc}")
            opened = False
        self.status_lbl.setText(
            f"Статус: {title} — " + ("открыт в браузере" if opened
                                     else "ссылка: " + clean_url))
        if not opened:
            QMessageBox.information(self, "Ссылка на трек", clean_url)
        return opened

    def _play_file(self, path, title, author=None, cover="", url="", index=None,
                   from_cache=False):
        """Загружает файл в pygame и сразу обновляет панель Now Playing."""
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self.current_volume)
            pygame.mixer.music.play()
        except Exception as e:
            self.status_lbl.setText("Статус: Не удалось воспроизвести файл")
            print(f"Техническая ошибка: {e}")
            return False
        self.current_audio_file = path
        self.current_url = url
        self.current_index = index
        self.is_playing = True
        self._was_busy = False
        self.is_paused = False
        self.btn_pause.setText("⏸ Пауза")
        suffix = " (мгновенно, из кэша)" if from_cache else ""
        self.status_lbl.setText(f"Играет: {title}{suffix}")

        # Вычисляем длительность и вейвформу трека
        try:
            snd = pygame.mixer.Sound(path)
            self._track_duration = float(snd.get_length() or 0.0)
        except Exception:
            self._track_duration = 0.0
        self._track_offset = 0.0
        self._play_start_ticks = pygame.time.get_ticks()

        amps = self._compute_waveform(path)
        if amps:
            self.waveform.set_real_data(amps)

        self._update_time_labels(0.0, self._track_duration)
        if hasattr(self, "timeline_slider") and not self._user_seeking:
            self.timeline_slider.setValue(0)

        self.set_now_playing(title, author=author or "СвязьКлауд", cover_path=cover)
        if index is not None:
            self._update_playlist_item(index, title=title, author=author, cover=cover)
        # Караоке: если рядом с треком уже есть <трек>.lrc, включаем режим «по ритму»
        if self.apply_lrc(path):
            self.status_lbl.setText("Статус: Текст песни идёт по ритму (.lrc)")
        return True


    def _format_time(self, seconds):
        sec = max(0, int(seconds))
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def _update_time_labels(self, cur_sec, total_sec):
        if hasattr(self, "time_cur_lbl"):
            self.time_cur_lbl.setText(self._format_time(cur_sec))
        if hasattr(self, "time_total_lbl"):
            self.time_total_lbl.setText(self._format_time(total_sec))

    def _update_timeline_playback(self):
        """Регулярный опрос позиции трека и синхронизация слайдера и вейвформы."""
        if not self.is_playing or self.is_paused or self._user_seeking:
            return
        if self._track_duration <= 0:
            return
        try:
            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms < 0:
                return
            cur_sec = min(self._track_duration, self._track_offset + (pos_ms / 1000.0))
            ratio = cur_sec / self._track_duration
            self._update_time_labels(cur_sec, self._track_duration)
            # Караоке: подсвечиваем строку, которая звучит именно сейчас
            if self.lyrics_label.has_lrc():
                self.lyrics_label.set_position(cur_sec)

            if hasattr(self, "timeline_slider"):
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(int(ratio * 1000))
                self.timeline_slider.blockSignals(False)
            if hasattr(self, "waveform"):
                self.waveform.set_progress(ratio)
        except Exception:
            pass

    def _on_seek_pressed(self):
        self._user_seeking = True

    def _on_seek_moved(self, val):
        if self._track_duration > 0:
            cur_sec = (val / 1000.0) * self._track_duration
            self._update_time_labels(cur_sec, self._track_duration)
            if hasattr(self, "waveform"):
                self.waveform.set_progress(val / 1000.0)

    def _on_seek_released(self):
        self._user_seeking = False
        if not self.current_audio_file or self._track_duration <= 0:
            return
        val = self.timeline_slider.value()
        target_sec = (val / 1000.0) * self._track_duration
        target_sec = max(0.0, min(self._track_duration - 0.5, target_sec))
        try:
            pygame.mixer.music.play(start=target_sec)
            self._track_offset = target_sec
            self._play_start_ticks = pygame.time.get_ticks()
            if self.is_paused:
                pygame.mixer.music.pause()
            if hasattr(self, "waveform"):
                self.waveform.set_progress(val / 1000.0)
        except Exception as e:
            print(f"Seek error: {e}")

    def toggle_repeat(self):
        self.is_repeat = self.btn_repeat.isChecked()
        self.btn_repeat.setStyleSheet(self._repeat_btn_style())
        state_str = "включен" if self.is_repeat else "выключен"
        self.status_lbl.setText(f"Статус: Повтор трека {state_str}")

    def play_prev_track(self):
        """Переход к предыдущему треку плейлиста."""
        if not self.playlist_tracks:
            return
        cur = self.current_index if self.current_index is not None else 0
        self._open_track_at((cur - 1) % len(self.playlist_tracks))

    def play_next_track(self):
        """Переход к следующему треку плейлиста."""
        if not self.playlist_tracks:
            return
        cur = self.current_index if self.current_index is not None else -1
        self._open_track_at((cur + 1) % len(self.playlist_tracks))

    def _check_track_end(self):
        """Ловит окончание трека и включает следующий из плейлиста (или повторяет)."""
        if not self.is_playing or self.is_paused:
            return
        try:
            busy = pygame.mixer.music.get_busy()
        except Exception:
            return
        if busy:
            self._was_busy = True
        elif self._was_busy:
            self._was_busy = False
            if self.is_repeat:
                self._restart_current_track()
            else:
                self._advance_to_next()

    def _restart_current_track(self):
        """Перезапуск текущего трека по кругу."""
        if self.current_audio_file and os.path.exists(self.current_audio_file):
            try:
                pygame.mixer.music.play(start=0.0)
                self._track_offset = 0.0
                self._play_start_ticks = pygame.time.get_ticks()
                self.is_playing = True
                self.is_paused = False
                self.btn_pause.setText("⏸ Пауза")
                if hasattr(self, "timeline_slider"):
                    self.timeline_slider.setValue(0)
                if hasattr(self, "waveform"):
                    self.waveform.set_progress(0.0)
                self.status_lbl.setText("Статус: Повтор трека сначала")
            except Exception as e:
                print(f"Restart error: {e}")
        elif self.current_index is not None and self.current_index < len(self.playlist_tracks):
            self._open_track_at(self.current_index)

    def _advance_to_next(self):
        """Автопереход к следующему треку плейлиста."""
        index = self.current_index
        if index is None:
            self.is_playing = False
            self.status_lbl.setText("Статус: Трек закончился")
            return
        if index + 1 >= len(self.playlist_tracks):
            self.is_playing = False
            self.status_lbl.setText("Статус: Плейлист закончился")
            return
        self.is_playing = False
        next_index = index + 1
        self.status_lbl.setText("Статус: Следующий трек...")
        QApplication.processEvents()
        self._open_track_at(next_index)

    def change_volume(self, val):
        """Громкость в процентах — сразу применяется к pygame."""
        self.current_volume = max(0.0, min(1.0, val / 100.0))
        try:
            pygame.mixer.music.set_volume(self.current_volume)
        except Exception:
            pass
        self.volume_val_lbl.setText(f"{val}%")

    def toggle_pause(self):
        """Пауза / продолжение / остановка — одной кнопкой.

        Кнопки «Остановить музыку» больше нет, поэтому состояния такие:
          * играет      -> ⏸ Пауза (ставим на паузу, надпись меняется);
          * на паузе    -> ▶ Продолжить (возобновляем с того же места);
          * остановлено -> ▶ Старт (тот же трек начинается сначала).
        """
        try:
            busy = pygame.mixer.music.get_busy()
        except Exception:
            busy = False
        if not busy and not self.is_paused:
            # Музыка не играет: кнопка запускает текущий трек заново
            if self.is_repeat and self.current_audio_file:
                self._restart_current_track()
            elif self.current_index is not None:
                self._open_track_at(self.current_index)
            elif self.current_url:
                self._start_track(self.current_url, self.current_index)
            else:
                self.status_lbl.setText("Статус: Нет активного трека")
            return
        if self.is_paused:
            pygame.mixer.music.unpause()
            self.is_paused = False
            self._refresh_pause_button()
            self.status_lbl.setText("Статус: Продолжаю воспроизведение")
        else:
            pygame.mixer.music.pause()
            self.is_paused = True
            self._refresh_pause_button()
            self.status_lbl.setText("Статус: Пауза")

    def _refresh_pause_button(self):
        """Обновляет надпись кнопки паузы под текущее состояние звука."""
        if self.is_paused:
            self.btn_pause.setText("▶ Продолжить")
            return
        try:
            playing = pygame.mixer.music.get_busy() or self.is_playing
        except Exception:
            playing = self.is_playing
        self.btn_pause.setText("⏸ Пауза" if playing else "▶ Старт")

    def stop_music(self):
        """Полная остановка звука. Кнопки нет — используется программно и в тестах."""
        pygame.mixer.music.stop()
        self._stop_whisper_thread()
        self.is_playing = False
        self.is_paused = False
        self.btn_pause.setText("▶ Старт")
        self.status_lbl.setText("Статус: Стоп")


    def closeEvent(self, event):
        """Корректное закрытие: останавливаем звук, поиск текста и распознавание."""
        web, self._web_thread = self._web_thread, None
        self._web_key = ""
        if web is not None:
            web.requestInterruption()
        search, self._search_thread = self._search_thread, None
        if search is not None and search.isRunning():
            search.wait(1500)      # поиск короткий: даём ему завершиться
        thread, self._whisper_thread = self._whisper_thread, None
        self._whisper_key = ""
        if thread is not None:
            thread.requestInterruption()
            if thread.isRunning():
                thread.wait(4000)   # не даём Qt удалить живой поток при выходе
        check, self._vk_check_thread = self._vk_check_thread, None
        if check is not None and check.isRunning():
            check.wait(3000)      # проверка токена короткая: даём ей завершиться
        auto, self._vk_auto_thread = self._vk_auto_thread, None
        if auto is not None and auto.isRunning():
            auto.requestInterruption()
            auto.wait(3000)      # авто-вход: не оставляем живой поток на выходе
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
        super().closeEvent(event)

    def resizeEvent(self, event):
        self.bg_label.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    player = SvyazCloudPlayerCustom()
    player.show()
    sys.exit(app.exec())
