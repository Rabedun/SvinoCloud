"""Поиск текста песни в интернете — до того, как браться за Whisper.

Источники (бесплатные, без регистрации):
  * lrclib.net  — открытая база, хорошо покрывает русский рок и рэп;
  * lyrics.ovh  — старый открытый API, иногда находит то, чего нет в lrclib;
  * Genius      — только через официальный API и личный токен
                   (set GENIUS_TOKEN=...); без токена сайт отдаёт 403.

Если текста нигде нет — вызывающий код запускает Whisper.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = os.environ.get("SVINOKLAUD_LYRICS_UA", "Mozilla/5.0 (Svinoklaud-lyrics)")
GENIUS_TOKEN = os.environ.get("GENIUS_TOKEN", "").strip()
# Как долго ждём ответ сайта. Таймаут небольшой: лучше быстро уйти в Whisper,
# чем держать пользователя перед пустым экраном.
TIMEOUT = float(os.environ.get("SVINOKLAUD_LYRICS_TIMEOUT", "6"))


def _get(url, headers=None, timeout=None):
    """Запрос с User-Agent. Возвращает текст ответа или бросает исключение."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
        raw = resp.read()
    return raw.decode("utf-8", "replace")


# --- Приводим название трека к виду, в котором оно есть в базах ---------------
NOISE = re.compile(
    r"\((?:[^()]*?(?:official|video|audio|lyric|lyrics|visualizer|clip|mv|hd|4k|"
    r"remaster(?:ed)?|live|cover|instrumental|explicit|version)[^()]*?)\)"
    r"|\[[^\[\]]*?(?:official|video|audio|lyric|lyrics|visualizer|clip|hd|4k|"
    r"remaster(?:ed)?|live|cover|instrumental|explicit|version)[^\[\]]*?\]",
    re.IGNORECASE)
FEAT = re.compile(r"\s*[([]?\b(?:feat|ft|featuring)\.?\s+[^)\]]+[)\]]?", re.IGNORECASE)


def clean_title(title):
    """«Песня (Official Video) [Lyrics]» -> «Песня»."""
    if not title:
        return ""
    text = NOISE.sub(" ", title)
    text = FEAT.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—|·:")
    return text or title.strip()


def title_variants(title):
    """Варианты запроса: как есть, без мусора в скобках, без исполнителя через дефис."""
    base = (title or "").strip()
    out = []
    for value in (base, clean_title(base)):
        if value and value not in out:
            out.append(value)
    # «Земфира - Искала» -> ещё и «Искала»: в базах исполнитель лежит отдельным полем
    for value in list(out):
        for sep in (" - ", " – ", " — ", " – "):
            if sep in value:
                tail = value.split(sep)[-1].strip()
                if tail and tail not in out:
                    out.append(tail)
    return out


def _plain(text):
    """Убираем LRC-таймкоды и лишние пробелы."""
    text = re.sub(r"\[\d{1,2}:\d{1,2}(?:[.:]\d{1,3})?\]", " ", text or "")
    return " ".join(text.split())


def _norm(value):
    """Нормализуем строку для сравнения: без регистра, лишних знаков и пробелов."""
    value = (value or "").lower().replace("ё", "е")
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _words(value):
    """Значимые слова строки (длиннее двух букв) — по ним судим о совпадении."""
    return [w for w in _norm(value).split() if len(w) > 2]


def matches_track(track_name, artist_name, wanted_title, wanted_artist):
    """Совпадает ли найденная песня с тем, что мы ищем.

    Без этой проверки поиск по любому слову подсовывал текст ЧУЖОЙ песни:
    запрос «в простонародье» находил «Искала» (слово «простонародье» есть в её
    тексте, а не в названии). Поэтому требуем, чтобы название из базы совпало.
    """
    got_title, want_title = _norm(track_name), _norm(wanted_title)
    if not got_title or not want_title:
        return False
    got_words, want_words = set(_words(track_name)), set(_words(wanted_title))
    if not got_words or not want_words:
        return False
    # Название совпало: либо целиком, либо один вариант содержит другой
    if not (got_title == want_title
            or want_words <= got_words or got_words <= want_words):
        return False
    # Исполнитель должен совпадать тоже. Иначе база отдаст одноимённый трек
    # другого артиста (ремикс, кавер) — это тихая подмена чужого текста.
    if wanted_artist and artist_name:
        got_artist, want_artist = _norm(artist_name), _norm(wanted_artist)
        if got_artist and want_artist and not (
                want_artist in got_artist or got_artist in want_artist):
            return False
    return True


# --- Источники ----------------------------------------------------------------
def from_lrclib(title, artist):
    """lrclib: точный запрос artist+track, при 503 — повтор, затем общий поиск."""
    attempts = []
    for track in title_variants(title):
        attempts.append(
            "https://lrclib.net/api/get?artist_name="
            + urllib.parse.quote(artist) + "&track_name=" + urllib.parse.quote(track))
    for track in title_variants(title):
        query = f"{artist} {track}".strip() or track
        attempts.append("https://lrclib.net/api/search?q=" + urllib.parse.quote(query))
    for url in attempts:
        for attempt in range(2):          # 503 у lrclib бывает — повторяем один раз
            try:
                data = json.loads(_get(url))
            except urllib.error.HTTPError as exc:
                if exc.code == 503 and attempt == 0:
                    time.sleep(0.7)
                    continue
                break
            except Exception:
                break
            text, hit_title, hit_artist = "", "", ""
            if isinstance(data, dict):
                text = data.get("plainLyrics") or data.get("syncedLyrics") or ""
                hit_title = data.get("trackName") or ""
                hit_artist = data.get("artistName") or ""
            elif isinstance(data, list):
                for item in data:            # ищем в выдаче именно нашу песню
                    name = item.get("trackName") or ""
                    if not matches_track(name, item.get("artistName") or "", title, artist):
                        continue
                    text = _plain(item.get("plainLyrics") or item.get("syncedLyrics") or "")
                    hit_title, hit_artist = name, item.get("artistName") or ""
                    if len(text) > 40:
                        return text
                    break
                else:
                    continue                # ни одна не подошла — следующий запрос
            text = _plain(text)
            if len(text) > 40 and matches_track(hit_title, hit_artist, title, artist):
                return text
            break
    return ""


def from_lyrics_ovh(title, artist):
    """lyrics.ovh: простой API по паре исполнитель/название."""
    for track in title_variants(title):
        for who in ((artist, track), (track, artist)):
            if not who[0] or not who[1]:
                continue
            url = ("https://api.lyrics.ovh/v1/"
                   + urllib.parse.quote(who[0]) + "/" + urllib.parse.quote(who[1]))
            try:
                text = " ".join(_get(url).split())
            except Exception:
                continue
            if len(text) > 40:
                return text
    return ""


def from_genius(title, artist):
    """Genius: официальный API с личным токеном (бесплатно выдают на сайте).

    Genius отдаёт текст только по API и только с токеном: без него ответ 401,
    а страница поиска рендерится через JS и в HTML результатов нет. Поэтому
    парсить нечего — нужен токен (см. load_token).
    """
    token = load_token()
    if not token:
        return ""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    queries = [f"{artist} {title}".strip(), clean_title(title)]
    hits = []
    for query in dict.fromkeys(q for q in queries if q):
        try:
            data = json.loads(_get("https://api.genius.com/search?q="
                                   + urllib.parse.quote(query), headers))
        except Exception:
            continue
        hits.extend((data.get("response") or {}).get("hits") or [])
        if hits:
            break
    for hit in hits:
        song = hit.get("result") or {}
        if not song.get("id"):
            continue
        hit_title = song.get("title") or song.get("name") or ""
        hit_artist = (song.get("artist_names") or song.get("primary_artist")
                      or {}).get("name") or "" if isinstance(
            song.get("artist_names") or song.get("primary_artist"), dict) else (
            song.get("artist_names") or song.get("primary_artist") or "")
        # Название должно совпасть: у Genius оно часто с «(Lyrics)» — чистим
        if not matches_track(clean_title(hit_title), hit_artist, title, artist):
            continue
        try:
            page = json.loads(_get("https://api.genius.com/songs/"
                                   + str(song.get("id")), headers))
        except Exception:
            continue
        text = ((page.get("response") or {}).get("song") or {}).get("lyrics") or ""
        text = " ".join(text.split())
        if len(text) > 40:
            return text
    return ""


SOURCES = (("Genius", from_genius), ("lrclib", from_lrclib),
           ("lyrics.ovh", from_lyrics_ovh))


def find(title, artist="", verbose=False):
    """Ищем текст по всем источникам. Возвращает (текст, источник)."""
    if not title or not str(title).strip():
        return "", ""
    artist = clean_title(artist or "")
    for name, fetch in SOURCES:
        try:
            text = fetch(title, artist)
        except Exception as exc:           # сайт упал — идём к следующему
            if verbose:
                print(f"lyrics: {name} не сработал ({type(exc).__name__})")
            continue
        if text:
            if verbose:
                print(f"lyrics: нашли в {name}, {len(text)} симв.")
            return text, name
        if verbose:
            print(f"lyrics: {name} — не найдено")
    return "", ""


def is_enabled():
    """Поиск в интернете можно отключить одной переменной окружения."""
    return os.environ.get("SVINOKLAUD_LYRICS_WEB", "1").strip() not in ("0", "off", "no")


# --- Токен Genius: из настроек плеера или переменной окружения -----------------
def _token_path():
    """Файл с токеном — рядом с кэшем, чтобы настройка пережила перезапуск."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sc_cache")
    return os.path.join(folder, "genius_token.txt")


def load_token():
    """Токен Genius: сначала файл настроек, затем переменная окружения."""
    try:
        with open(_token_path(), "r", encoding="utf-8") as fh:
            saved = fh.read().strip()
        if saved:
            return saved
    except OSError:
        pass
    return GENIUS_TOKEN


def save_token(token):
    """Сохраняем токен в файл. Возвращает True, если записали."""
    token = (token or "").strip()
    try:
        os.makedirs(os.path.dirname(_token_path()), exist_ok=True)
        with open(_token_path(), "w", encoding="utf-8") as fh:
            fh.write(token)
        return True
    except OSError:
        return False
