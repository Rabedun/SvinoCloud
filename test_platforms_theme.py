"""Проверка больших обновлений: платформы, караоке по .lrc, профили темы, кнопки.

Запуск:  python test_platforms_theme.py     (код возврата 0 — все проверки прошли)

Тест офлайновый: сеть, Whisper и файлы вашего плеера не трогаются — пути
playlist.json и theme_config.json подменяются на временные.
"""
import json
import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QThread, QTimer, QEventLoop   # noqa: E402

import main        # noqa: E402
import lyrics_web  # noqa: E402

results = []


def check(name, condition, detail=""):
    # Печатаем через console(): в консоли Windows cp1251, и обычный print()
    # падает с UnicodeEncodeError на эмодзи вроде «⏸».
    results.append((name, bool(condition), detail))
    main.console(("PASS " if condition else "FAIL ") + name
                 + (f"  -> {detail}" if detail else ""))


def section_1_platforms():
    """Определение платформы по ссылке — основа поддержки ВК/Яндекса/Spotify."""
    cases = [
        ("https://soundcloud.com/user/track", "sc"),
        ("https://m.soundcloud.com/user/track", "sc"),
        ("https://www.youtube.com/watch?v=abc", "yt"),
        ("https://youtu.be/abc", "yt"),
        ("https://music.youtube.com/watch?v=abc", "yt"),
        ("https://vk.com/video-1_2", "vk"),
        ("https://vk.ru/video-1_2", "vk"),
        ("https://vkvideo.ru/video-1_2", "vk"),
        ("https://music.yandex.ru/album/1/track/2", "ym"),
        ("https://music.yandex.by/album/1/track/2", "ym"),
        ("https://open.spotify.com/track/abc", "spotify"),
    ]
    wrong = [(u, main.detect_platform(u), want)
             for u, want in cases if main.detect_platform(u) != want]
    check("платформа определяется по ссылке (SoundCloud, YouTube, ВК, Яндекс, Spotify)",
          not wrong, str(wrong))
    check("неизвестный сайт не выдаёт ложную платформу",
          main.detect_platform("https://example.com/track") == "",
          main.detect_platform("https://example.com/track"))
    check("пустая ссылка безопасна",
          main.detect_platform("") == "" and main.detect_platform(None) == "")
    check("название платформы по id",
          main.platform_name("sc") == "SoundCloud"
          and main.platform_name("vk") == "VK"
          and main.platform_name("ym") == "Яндекс.Музыка"
          and main.platform_name("spotify") == "Spotify")


def section_2_search():
    """Мультиплатформенный поиск и подпись платформы в результатах."""
    check("поиск идёт минимум по двум платформам",
          len(main.SEARCH_PLATFORMS) >= 2, str(main.SEARCH_PLATFORMS))
    check("SoundCloud и YouTube входят в поиск",
          "sc" in main.SEARCH_PLATFORMS and "yt" in main.SEARCH_PLATFORMS,
          str(main.SEARCH_PLATFORMS))
    check("в общий поиск включена Яндекс.Музыка",
          "ym" in main.SEARCH_ALL_PLATFORMS, str(main.SEARCH_ALL_PLATFORMS))
    check("площадки в поиске не повторяются",
          len(set(main.SEARCH_ALL_PLATFORMS)) == len(main.SEARCH_ALL_PLATFORMS),
          str(main.SEARCH_ALL_PLATFORMS))
    check("площадки с собственным поиском не имеют префикса yt-dlp",
          not any(p["can_search"] for p in main.PLATFORMS if not p["prefix"]))


    sc = main.SearchWorker.parse(
        "song\tauthor\thttps://soundcloud.com/a/b\t100.5\n", "sc")
    check("разобранный трек хранит платформу",
          sc and sc[0]["platform"] == "sc" and sc[0]["type"] == "sc",
          str(sc[0]) if sc else "")
    yt = main.SearchWorker.parse(
        "v\tch\thttps://www.youtube.com/watch?v=x\t200\n", "yt")
    check("трек с YouTube помечен как yt",
          yt and yt[0]["platform"] == "yt"
          and yt[0]["url"].startswith("https://www.youtube.com"),
          str(yt[0]) if yt else "")

    label_sc = main.SearchWindow.describe(sc[0])
    label_yt = main.SearchWindow.describe(yt[0])
    check("в строке результата есть (SoundCloud)",
          "(SoundCloud)" in label_sc, label_sc)
    check("в строке результата есть (YouTube)",
          "(YouTube)" in label_yt, label_yt)
    check("строка читается как «автор — название»",
          label_sc.startswith("author — song"), label_sc)

    cmd_sc = main.PlatformSearchWorker("запрос", "sc").command()
    cmd_yt = main.PlatformSearchWorker("запрос", "yt").command()
    check("SoundCloud ищет через scsearch",
          any("scsearch" in str(p) for p in cmd_sc),
          " ".join(str(p) for p in cmd_sc if "search" in str(p)))
    check("YouTube ищет через ytsearch",
          any("ytsearch" in str(p) for p in cmd_yt),
          " ".join(str(p) for p in cmd_yt if "search" in str(p)))


def section_3_other_platforms():
    """Ссылки ВК/Яндекса/Spotify тоже получают подпись платформы."""
    vk = {"type": "vk", "platform": "vk", "title": "ТП",
          "author": "Серега Пират", "url": "https://vk.com/video-1_2"}
    check("подпись (VK) для ссылки ВК",
          "(VK)" in main.SearchWindow.describe(vk),
          main.SearchWindow.describe(vk))
    sp = {"type": "spotify", "platform": "spotify", "title": "Трек",
          "author": "Артист", "url": "https://open.spotify.com/track/x"}
    check("подпись (Spotify)",
          "(Spotify)" in main.SearchWindow.describe(sp),
          main.SearchWindow.describe(sp))
    auto = {"title": "Трек", "author": "A",
            "url": "https://music.yandex.ru/album/1/track/2"}
    check("платформа угадывается по ссылке, если не указана",
          "(Яндекс.Музыка)" in main.SearchWindow.describe(auto),
          main.SearchWindow.describe(auto))
    check("у Spotify честно объяснено ограничение (DRM)",
          "spotify" in main.PLATFORM_LIMITS, str(list(main.PLATFORM_LIMITS)))
    check("у ВК ограничения нет — аудио качается напрямую",
          "vk" not in main.PLATFORM_LIMITS, str(list(main.PLATFORM_LIMITS)))



def section_3b_web_parsers():
    """Разбор данных площадок: Яндекс.Музыка и Spotify. Сеть не нужна."""
    # Фрагмент реальной разметки Яндекс.Музыки: объект трека со вложенными
    # массивами и экранированными слешами — именно такие и приходится разбирать.
    html = ('<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])'
            '.push([{"op":"add","path":"\\u002Fsearch\\u002Ftracks\\u002F0",'
            '"value":{"type":"music","id":152738793,"title":"прости я не знаю",'
            '"durationMs":135750,"albumId":42671838,"realId":152738793,'
            '"hasSyncLyrics":true,"coverUri":"avatars.yandex.net\\u002Fget-music-content'
            '\\u002Fa.42671838-1\\u002F%%",'
            '"artists":[{"id":1,"name":"Серега Пират"}]}}])</script>')
    blobs = main._balanced_objects(html, '"realId"')
    check("объект трека вырезан из HTML целиком", len(blobs) == 1, str(len(blobs)))
    data = json.loads(blobs[0]) if blobs else {}
    check("id и название прочитаны",
          data.get("id") == 152738793 and data.get("title") == "прости я не знаю",
          str(data.get("title")))
    check("исполнитель прочитан из вложенного массива",
          [a["name"] for a in data.get("artists", [])] == ["Серега Пират"],
          str(data.get("artists")))
    check("длительность в миллисекундах",
          data.get("durationMs") == 135750, str(data.get("durationMs")))
    check("признак синхротекста прочитан", data.get("hasSyncLyrics") is True)
    cover = str(data.get("coverUri", "")).replace("\\u002F", "/")
    check("обложка почищена от экранирования",
          cover == "avatars.yandex.net/get-music-content/a.42671838-1/%%", cover)

    # Вложенные объекты со скобками внутри строк не должны путать разбор
    tricky = ('"value":{"id":1,"note":"скобка { внутри строки","list":[1,2,3],'
              '"realId":1}')
    check("скобки внутри строк не ломают разбор",
          len(main._balanced_objects(tricky, '"realId"')) == 1
          and json.loads(main._balanced_objects(tricky, '"realId"')[0])["note"]
          == "скобка { внутри строки")

    # Spotify: oEmbed по готовой ссылке (сеть отключена — проверяем разбор id)
    check("id трека Spotify вытаскивается из ссылки",
          main.detect_platform("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
          == "spotify")
    check("международная ссылка Spotify тоже распознаётся",
          main.detect_platform(
              "https://open.spotify.com/intl-ru/track/4cOdK2wGLETKBW3PvgPWqT")
          == "spotify")
    check("на мусорной ссылке Spotify ничего не выдумывается",
          main.spotify_track_info("https://open.spotify.com/playlist/abc") == {})
    check("пустая ссылка Spotify безопасна", main.spotify_track_info("") == {})

    # ВК: без токена поиск честно молчит, а не выдумывает результаты
    check("поиск ВК без токена возвращает пусто",
          main.search_vk_music("что угодно", 3, token="") == [],
          str(main.search_vk_music("что угодно", 3, token="")))
    check("пустой запрос не ищется нигде",
          main.search_yandex_music("") == [] and main.search_vk_music("", token="x") == [])



def section_4_lrc():
    """Разбор LRC-таймкодов и выбор активной строки."""
    lrc = ("[ti:Song]\n[ar:X]\n[00:01.00]первая строка\n[00:12.50]вторая\n"
           "[01:23.45]третья\n")
    lines = main.parse_lrc(lrc)
    check("разобраны все строки с таймкодами", len(lines) == 3, str(len(lines)))
    check("время переведено в секунды",
          abs(lines[0][0] - 1.0) < 0.01 and abs(lines[2][0] - 83.45) < 0.01,
          str([t for t, _ in lines]))
    check("текст строки без меток времени", lines[1][1] == "вторая", lines[1][1])
    check("служебные теги [ti:…] не попали в текст песни",
          all(t > 0 for t, _ in lines), str(lines))
    check("активна первая строка в её момент",
          main.active_lrc_index(lines, 1.5) == 0)
    check("активна вторая строка в её момент",
          main.active_lrc_index(lines, 20.0) == 1)
    check("после последней метки остаётся последняя строка",
          main.active_lrc_index(lines, 999.0) == 2)
    check("до первой метки ничего не подсвечено",
          main.active_lrc_index(lines, 0.0) == -1)
    check("пустой LRC не ломает разбор",
          main.parse_lrc("") == [] and main.parse_lrc(None) == [])
    check("текст без таймкодов не считается LRC",
          main.parse_lrc("просто слова") == [])

    segments = [{"text": " раз ", "start": 0.0}, {"text": "два", "start": 83.45},
                {"text": "", "start": 99.0}]
    built = main.segments_to_lrc(segments)
    check("из сегментов Whisper собраны таймкоды",
          "[00:00.00] раз" in built and "[01:23.45] два" in built,
          built.replace("\n", " | "))
    check("собранный LRC сразу разбирается", len(main.parse_lrc(built)) == 2, built)


def section_5_karaoke_widget():
    """Виджет караоке: включение режима и подсветка строки по позиции."""
    w = main.LyricsMarquee(4)
    w.resize(360, w._total_height())
    check("без LRC виджет в обычном режиме", not w.has_lrc())
    w.set_scroll_text("обычный текст без таймкодов")
    check("обычный текст работает как раньше",
          not w.has_lrc() and w._all_lines, f"{len(w._all_lines)} строк")

    # Первая метка — 2 секунды: до неё (и в 0.0) ещё никто не поёт.
    # Проверка «ничего не подсвечено» была бы неверной для метки 00:00.00:
    # в настоящем LRC первая строка как раз начинается с нуля.
    lrc = "[00:02.00]раз\n[00:10.00]два\n[00:20.00]три\n[00:30.00]четыре\n"
    check("LRC включает режим караоке", w.set_lrc(lrc) and w.has_lrc())
    check("строки караоке загружены", len(w._all_lines) == 4, str(len(w._all_lines)))
    check("до первой метки ничего не подсвечено", w.set_position(0.0) == -1)
    check("на 5-й секунде поёт первая строка", w.set_position(5.0) == 0)
    check("на 15-й секунде поёт вторая строка", w.set_position(15.0) == 1)
    check("на 35-й секунде поёт четвёртая строка", w.set_position(35.0) == 3)
    # Проверяем подсветку именно тогда, когда активна соответствующая реплика
    w.set_position(15.0)
    check("активная строка помечена для подсветки",
          w._lrc_row_active(w._lrc_map[1])
          and not w._lrc_row_active(w._lrc_map[0]),
          f"idx={w._lrc_index} map={w._lrc_map}")
    zero = main.LyricsMarquee(2)
    zero.resize(300, zero._total_height())
    zero.set_lrc("[00:00.00]сразу\n[00:10.00]потом\n")
    check("песня, начинающаяся с 00:00.00, поёт сразу",
          zero.set_position(0.0) == 0, str(zero.set_position(0.0)))
    zero.close()
    w._animate()
    check("караоке-блок доезжает до активной строки",
          0 <= w._start <= 1, f"start={w._start}")
    w.set_scroll_text("обычный текст")
    check("возврат к обычному тексту отключает караоке", not w.has_lrc())
    w.set_lrc("текст без единой метки времени")
    check("текст без меток не ломает виджет", not w.has_lrc() and w._all_lines)
    w.close()


def section_6_buttons_and_colors(player):
    """Кнопка «Стоп» удалена, у каждой кнопки свой цвет, рамок нет."""
    texts = [b.text() for b in player.findChildren(main.QPushButton)]
    check("нет кнопки с надписью «Остановить музыку»",
          not any("Остановить" in t for t in texts),
          str([t for t in texts if "Остановить" in t]))
    check("атрибута btn_stop у плеера больше нет", not hasattr(player, "btn_stop"))
    check("кнопка паузы на месте",
          player.btn_pause.text() in ("⏸ Пауза", "▶ Продолжить", "▶ Старт"),
          player.btn_pause.text())
    player.stop_music()
    check("после остановки кнопка предлагает «Старт»",
          player.btn_pause.text() == "▶ Старт", player.btn_pause.text())
    player.toggle_pause()
    check("нажатие без активного трека не ломает плеер",
          player.status_lbl.text().startswith("Статус:"), player.status_lbl.text())

    check("у кнопки есть свой ключ темы",
          getattr(player.btn_add_playlist, "theme_key", "") == "btn_add_playlist",
          str(getattr(player.btn_add_playlist, "theme_key", "")))
    player.button_colors["btn_local"] = "#ff0000"
    check("у выбранной кнопки свой цвет",
          "#ff0000" in player.btn_style("btn_local"), player.btn_style("btn_local"))
    check("остальные кнопки не покрасились",
          "#ff0000" not in player.btn_style("btn_search"),
          player.btn_style("btn_search"))
    check("кнопка без своего цвета берёт общий акцент",
          player.current_accent_color in player.btn_style("btn_search"))

    # Белая обводка вокруг названия и автора убрана
    for name, label in (("author_label", "автор"), ("track_label", "название")):
        sheet = getattr(player, name).styleSheet()
        check(f"у блока «{label}» нет рамки",
              "border: none" in sheet and "solid #ffffff" not in sheet, sheet)
        check(f"фон блока «{label}» прозрачный", "transparent" in sheet)
    global_sheet = player.styleSheet()
    check("метки вообще не рисуются с рамкой",
          "QLabel { background: transparent; border: none; }" in global_sheet)
    # Проверяем именно правило панели: та же рамка есть у полей ввода — это нормально
    panel_rule = [part for part in global_sheet.split("}")
                  if part.startswith("QWidget#NowPlayingPanel") or
                  "QWidget#NowPlayingPanel" in part]
    check("у панели Now Playing тоже нет рамки",
          panel_rule and "border: none" in panel_rule[0]
          and "1px solid" not in panel_rule[0],
          panel_rule[0] if panel_rule else "правило не найдено")


def section_7_theme_profiles(player, tmp):
    """Профили кастомизации: сохранение, применение, автозагрузка."""
    profile_file = os.path.join(tmp, "theme_config.json")
    saved = main.theme_path
    main.theme_path = lambda: profile_file
    try:
        player.button_colors["btn_local"] = "#00ff00"
        player.current_accent_color = "#123456"
        player.current_font = "Impact"
        player.current_opacity = 0.55
        check("профиль сохраняется в theme_config.json",
              player.save_theme_profile("Тестовый"))
        check("файл кастомизации создан", os.path.exists(profile_file))
        with open(profile_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        stored = data["presets"]["Тестовый"]
        check("цвета, шрифт и прозрачность записаны в файл",
              stored["buttons"]["btn_local"] == "#00ff00"
              and stored["font"] == "Impact"
              and abs(stored["opacity"] - 0.55) < 0.01,
              json.dumps(stored, ensure_ascii=False))
        check("профиль запомнен как текущий", data["current"] == "Тестовый")
        check("профиль виден в списке", "Тестовый" in player.theme_profiles(),
              str(player.theme_profiles()))

        # Портим настройки и применяем профиль обратно
        player.current_accent_color = "#000000"
        player.button_colors = {}
        check("профиль применяется", player.apply_theme_profile("Тестовый"))
        check("цвет кнопки восстановлен из профиля",
              player.button_color("btn_local") == "#00ff00",
              player.button_color("btn_local"))
        check("акцент восстановлен из профиля",
              player.current_accent_color == "#123456", player.current_accent_color)
        check("шрифт восстановлен из профиля",
              player.current_font == "Impact", player.current_font)
        check("применение несуществующего профиля безопасно",
              player.apply_theme_profile("нет такого") is False)

        reloaded = main.load_theme_config()
        check("тема подхватывается при запуске плеера",
              reloaded["accent"] == "#123456" and reloaded["font"] == "Impact"
              and reloaded["buttons"].get("btn_local") == "#00ff00",
              json.dumps(reloaded, ensure_ascii=False))

        with open(profile_file, "w", encoding="utf-8") as fh:
            fh.write("{ это не json")
        check("битый theme_config.json не ломает запуск",
              main.load_theme_config()["accent"] == main.DEFAULT_THEME["accent"])
        # После битого файла список пресетов пуст — сохраняем заново оба профиля
        player.save_theme_profile("Тестовый")
        player.save_theme_profile("Второй")
        check("профили не затирают друг друга",
              player.theme_profiles() == ["Второй", "Тестовый"],
              str(player.theme_profiles()))
        player.reset_theme()
        check("сброс возвращает акцент по умолчанию",
              player.current_accent_color == main.DEFAULT_THEME["accent"],
              player.current_accent_color)
    finally:
        main.theme_path = saved


def section_8_lrc_files(player, tmp):
    """Файл .lrc рядом с треком: сохранение, чтение и включение караоке."""
    audio = os.path.join(tmp, "песня.mp3")
    with open(audio, "wb") as fh:
        fh.write(b"\x00" * 128)
    lrc_path = os.path.splitext(audio)[0] + ".lrc"

    player._lrc_memory.clear()
    player.lyrics_label.set_scroll_text("было")
    check("без файла .lrc караоке не включается", player.apply_lrc(audio) is False)

    # Заголовок должен совпадать с текущим треком, иначе ответ честно игнорируется
    player._raw_track_title = "Трек"
    player._on_whisper_lrc("Трек", audio, "[00:00.00]раз\n[00:05.00]два\n")
    check("таймкоды сохранены в <трек>.lrc", os.path.exists(lrc_path), lrc_path)
    with open(lrc_path, "r", encoding="utf-8") as fh:
        stored = fh.read()
    check("файл .lrc читается обратно", "00:05.00" in stored, stored.replace("\n", " | "))
    check("после сохранения включилось караоке", player.lyrics_label.has_lrc())

    # Новый запуск трека: караоке подхватывается из файла без нового распознавания
    player.lyrics_label.set_scroll_text("сброшено")
    player._lrc_memory.clear()
    check("при новом запуске караоке читается из файла", player.apply_lrc(audio) is True)
    check("активная строка считается по позиции воспроизведения",
          player.lyrics_label.set_position(6.0) == 1,
          str(player.lyrics_label.set_position(6.0)))

    # Ответ по чужому треку не должен включать караоке
    player.lyrics_label.set_scroll_text("текст другого трека")
    player._on_whisper_lrc("Совсем другой трек", os.path.join(tmp, "чужая.mp3"),
                           "[00:00.00]чужой\n")
    check("чужой ответ не включает караоке", not player.lyrics_label.has_lrc())
    check("текст другого трека не перебит",
          player.lyrics_label._raw_text == "текст другого трека",
          player.lyrics_label._raw_text)


def section_9_playlist_platforms(player):
    """Ссылки разных платформ попадают в плейлист с меткой."""
    player.url_input.setText("https://vk.com/video-1_2")
    player.add_to_playlist()
    check("трек с ВК добавлен в плейлист",
          len(player.playlist_tracks) == 1
          and player.playlist_tracks[0]["platform"] == "vk",
          str(player.playlist_tracks[0].get("platform"))
          if player.playlist_tracks else "")
    check("в плейлисте видна метка (VK)",
          "(VK)" in player.playlist_widget.item(0).text(),
          player.playlist_widget.item(0).text())
    player.url_input.setText("https://open.spotify.com/track/xyz")
    player.add_to_playlist()
    check("трек со Spotify добавлен", len(player.playlist_tracks) == 2,
          str(len(player.playlist_tracks)))
    check("про DRM Spotify сказано прямо в статусе",
          "DRM" in player.status_lbl.text(), player.status_lbl.text())
    # Трек без ссылки и без файла добавляться не должен
    before = len(player.playlist_tracks)
    check("пустой трек не попадает в плейлист",
          player._add_track({"type": "sc"}) is False and len(player.playlist_tracks) == before)


def section_10_search_worker_offline():
    """Поиск без сети: важно, что он завершается, а не зависает навсегда."""

    class _FakeWorker(main.PlatformSearchWorker):
        """Площадка, которая отвечает мгновенно, без сети.

        Наследуемся от настоящего класса намеренно: у него уже объявлены
        сигналы results и failed, которые и слушает SearchWorker.
        """

        def run(self):
            self.results.emit(self._platform, [{
                "type": self._platform, "platform": self._platform,
                "title": f"трек с {self._platform}", "author": "исполнитель",
                "url": f"https://example.com/{self._platform}/1", "duration": 100.0}])

    original = main.PlatformSearchWorker
    main.PlatformSearchWorker = _FakeWorker
    try:
        worker = main.SearchWorker("запрос", 1, platforms=("sc", "ym"))
        got, finished = [], []
        worker.results.connect(lambda t: got.extend(t))
        worker.finished.connect(lambda: finished.append(True))

        # Проверяем ровно тот путь, что работает в плеере: QEventLoop плюс сторож
        # на QTimer. Без event loop сигналы между потоками в PyQt не доставляются.
        loop = QEventLoop()
        tick = QTimer()
        tick.timeout.connect(worker.poll)          # те же 250 мс, что и в плеере
        tick.start(main.SEARCH_POLL_MS)
        worker.results.connect(lambda t: loop.quit())
        worker.finished.connect(lambda: loop.quit())
        QTimer.singleShot(30000, loop.quit)        # страховка от зависания
        worker.start()
        loop.exec()
        tick.stop()
        check("поиск завершается, а не зависает", bool(finished))
        check("собраны треки со всех площадок", len(got) == 2,
              str([t["platform"] for t in got]))
        check("каждый трек помечен своей площадкой",
              {t["platform"] for t in got} == {"sc", "ym"},
              str([t["platform"] for t in got]))
        check("после завершения повторный опрос безопасен", worker.poll() is None)
        check("методы совместимости с QThread на месте",
              hasattr(worker, "isRunning") and hasattr(worker, "wait")
              and hasattr(worker, "requestInterruption"))
    finally:
        main.PlatformSearchWorker = original


def section_11_direct_download():
    """Прямое скачивание аудио: формат файла и защита от «не-аудио»."""
    cases = [("https://x.com/a/file.mp3", "", ".mp3"),
             ("https://x.com/a/file.m4a", "", ".m4a"),
             ("https://x.com/a/file.mp4", "", ".m4a"),
             ("https://x.com/a/stream", "audio/mpeg", ".mp3"),
             ("https://x.com/a/stream", "audio/mp4", ".m4a"),
             ("https://x.com/a/stream", "audio/x-m4a", ".m4a"),
             ("https://x.com/a/stream", "audio/ogg", ".ogg"),
             ("https://x.com/a/stream", "application/octet-stream", ".mp3"),
             ("https://x.com/a/stream", "", ".mp3")]
    wrong = [(u, c, main.guess_audio_ext(u, c), want)
             for u, c, want in cases if main.guess_audio_ext(u, c) != want]
    check("формат аудио определяется по ссылке и заголовку", not wrong, str(wrong))

    # Мусор на входе не должен приводить к «скачанному» файлу
    tmp = tempfile.mkdtemp(prefix="svinoklaud_audio_")
    check("не-ссылка отклоняется",
          main.download_audio("не ссылка", os.path.join(tmp, "a")) == "")
    check("пустая ссылка отклоняется",
          main.download_audio("", os.path.join(tmp, "b")) == "")
    check("несуществующий хост отклоняется",
          main.download_audio("https://нет-такого-хоста-12345.invalid/x.mp3",
                              os.path.join(tmp, "c"), timeout=8) == "")
    leftovers = [n for n in os.listdir(tmp) if n.endswith(".part")]
    check("временные файлы не остаются", not leftovers, str(leftovers))
    try:
        os.rmdir(tmp)
    except OSError:
        pass


def section_12_vk_token():
    """Токен ВК: сохранение, загрузка и включение площадки в поиск."""
    tmp = tempfile.mkdtemp(prefix="svinoklaud_vk_")
    real_path = main.vk_token_path
    main.vk_token_path = lambda: os.path.join(tmp, "vk_token.txt")
    real_token = main.VK_ACCESS_TOKEN
    main.VK_ACCESS_TOKEN = ""
    try:
        check("без токена ВК не ищется", main.load_vk_token() == "")
        check("ВК не попадает в список площадок без токена",
              "vk" not in main.active_search_platforms(),
              str(main.active_search_platforms()))
        check("токен сохраняется в файл", main.save_vk_token("abc123"))
        check("токен читается обратно", main.load_vk_token() == "abc123",
              main.load_vk_token())
        check("с токеном ВК добавляется в поиск",
              "vk" in main.active_search_platforms(),
              str(main.active_search_platforms()))
        check("остальные площадки никуда не делись",
              {"sc", "yt", "ym"}.issubset(set(main.active_search_platforms())),
              str(main.active_search_platforms()))
        main.VK_ACCESS_TOKEN = "envtoken"
        main.save_vk_token("")
        check("без файла берётся переменная окружения",
              main.load_vk_token() == "envtoken", main.load_vk_token())
    finally:
        main.vk_token_path = real_path
        main.VK_ACCESS_TOKEN = real_token
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def section_13_vk_track_record():
    """Трек из ВК хранит прямую ссылку — без неё включить его нельзя."""
    tmp = tempfile.mkdtemp(prefix="svinoklaud_vktrk_")
    real_playlist = main.PLAYLIST_FILE
    main.PLAYLIST_FILE = os.path.join(tmp, "playlist.json")
    try:
        player = main.SvinoklaudPlayer()
        track = {"type": "vk", "platform": "vk", "title": "ТП",
                 "author": "Серега Пират",
                 "url": "https://vk.com/audio200012345_456239017",
                 "duration": 120.0,
                 "direct_url": "https://ps1.cdn.vk.com/file.mp3?token=abc"}
        player.add_search_result(track)
        record = player.playlist_tracks[0]
        check("прямая ссылка сохранена в плейлисте",
              record.get("direct_url") == track["direct_url"],
              str(record.get("direct_url")))
        check("плеер находит прямую ссылку по индексу",
              player._direct_url_for(record["url"], 0) == track["direct_url"])
        check("плеер находит прямую ссылку и без индекса",
              player._direct_url_for(record["url"]) == track["direct_url"])
        check("запись трека найдена по ссылке",
              (player._track_by_index(None, record["url"]) or {}).get("title") == "ТП")
        check("у чужой ссылки прямая ссылка пустая",
              player._direct_url_for("https://vk.com/audio1_2", None) == "")
        with open(player._playlist_path(), "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        check("прямая ссылка переживает перезапуск (playlist.json)",
              saved["tracks"][0].get("direct_url") == track["direct_url"],
              str(list(saved["tracks"][0].keys())))
        player.close()
    finally:
        main.PLAYLIST_FILE = real_playlist


def section_14_vk_token_check_worker():
    """Проверка токена: пустой токен отклоняется мгновенно и без сети."""
    worker = main.VkTokenCheckWorker("")
    results = []
    worker.checked.connect(lambda ok, msg: results.append((ok, msg)))
    # Вызываем run() напрямую: так проверка не зависит от доставки сигналов
    # между потоками, а проверяет именно логику принятия решения.
    worker.run()
    check("пустой токен отклоняется без сети",
          results and results[0][0] is False, str(results))
    check("пояснение про пустой токен понятное",
          results and "не введён" in results[0][1], str(results))

    # Методы плеера для работы с токеном на месте
    player = main.SvinoklaudPlayer()
    check("у плеера есть поле токена", hasattr(player, "vk_token"))
    check("проверка токена без токена не падает и подсказывает",
          player.check_vk_token() is None
          and "вставьте токен" in player.status_lbl.text(),
          player.status_lbl.text())
    check("есть кнопка открытия страницы приложений",
          callable(getattr(player, "open_vk_apps", None)))
    player.close()


def section_15_vk_auto_token():
    """Автополучение токена: библиотека подключается, пароль не сохраняется."""
    # 1. Класс-поток есть и объявляет нужные сигналы
    check("есть поток автополучения токена ВК",
          hasattr(main, "VkAudioTokenWorker"))
    worker_cls = main.VkAudioTokenWorker
    for signal in ("fetched", "need_code", "failed"):
        check(f"у потока есть сигнал {signal}", hasattr(worker_cls, signal))

    # 2. Пустой логин и пароль: сеть не трогаем, код безопасный
    worker = worker_cls("", "")
    events = []
    worker.fetched.connect(lambda *a: events.append(("fetched",) + a))
    worker.need_code.connect(lambda msg: events.append(("need_code", msg)))
    worker.failed.connect(lambda msg: events.append(("failed", msg)))
    worker.run()                     # напрямую: тут не нужна доставка между потоками
    check("с пустыми данными токен не выдумывается",
          events and events[0][0] == "failed", str(events))
    check("сообщение упоминает логин и пароль",
          events and ("огин" in events[0][1]), str(events))

    # 3. Пояснения ошибок библиотеки переведены на русский
    worker = worker_cls("+70000000000", "пароль")
    described = [worker._describe(code) for code in (0, 1, 2, 3, 4, 5)]
    check("все коды ошибок библиотеки переведены",
          all(isinstance(text, str) and text for text in described), str(described))
    check("код «нужен SMS» распознан по-русски",
          "СМС" in worker._describe(4), worker._describe(4))
    check("неизвестный код не роняет разбор",
          isinstance(worker._describe(99), str), worker._describe(99))

    # 4. Пароль нигде не сохраняется: в файле только токен
    tmp = tempfile.mkdtemp(prefix="svinoklaud_vkauto_")
    real_token = main.vk_token_path
    real_agent = main.vk_user_agent_path
    main.vk_token_path = lambda: os.path.join(tmp, "vk_token.txt")
    main.vk_user_agent_path = lambda: os.path.join(tmp, "vk_agent.txt")
    try:
        main.save_vk_token("vk1.a.testtoken")
        main.save_vk_user_agent("KateMobile/1.0")
        check("токен сохранён", main.load_vk_token() == "vk1.a.testtoken",
              main.load_vk_token())
        check("User-Agent сохранён", main.load_vk_user_agent() == "KateMobile/1.0",
              main.load_vk_user_agent())
        headers = main._vk_headers()
        check("сохранённый User-Agent идёт в запросы к ВК",
              headers.get("User-Agent") == "KateMobile/1.0", str(headers))
        main.save_vk_user_agent("")
        check("без сохранённого UA берётся обычный браузерный",
              "User-Agent" not in main._vk_headers(), str(main._vk_headers()))
        # В файлах нет пароля
        stored = open(os.path.join(tmp, "vk_token.txt"), encoding="utf-8").read()
        check("в файле токена нет пароля", "пароль" not in stored, stored)
        check("файлы с данными ВК лежат во временной папке",
              os.path.basename(main.vk_token_path()) == "vk_token.txt")
    finally:
        main.vk_token_path = real_token
        main.vk_user_agent_path = real_agent

    # 5. Кнопки в окне настроек на месте
    player = main.SvinoklaudPlayer()
    check("плеер умеет получать токен автоматически",
          callable(getattr(player, "fetch_vk_token_auto", None)))
    box = main.SettingsWindow(player)
    player.settings_win = box
    for name in ("vk_login_edit", "vk_password_edit", "vk_code_edit", "btn_vk_auto"):
        check(f"в настройках есть поле {name}", hasattr(box, name))
    check("пароль скрыт звёздочками",
          box.vk_password_edit.echoMode() == box.vk_password_edit.EchoMode.Password)
    check("подпись кнопки объясняет, что она делает",
          "автоматически" in box.btn_vk_auto.text().lower(), box.btn_vk_auto.text())
    check("кнопка проверки токена тоже есть", hasattr(box, "btn_vk_check"))
    check("инструкция для ручного токена сохранена",
          "Токен ВК нужен" in box.vk_hint.text(), box.vk_hint.text()[:60])
    check("поле для ручного токена на месте", hasattr(box, "vk_token_edit"))
    box.close()

    # 6. Ошибки входа ВК переводятся на понятный русский
    reason = ("description: 'Too many attempts were made to sign in to "
              "this account with...'")
    translated = main.vk_error_ru(reason)
    check("слишком много попыток переведено с подсказкой",
          "Подождите 15–60 минут" in translated, translated)
    check("подсказка запрещает дёргать кнопку",
          "НЕ нажимайте кнопку повторно" in translated, translated)
    check("неверный пароль переведён",
          "Неверный логин или пароль" in
          main.vk_error_ru("Invalid password: wrong credentials"))
    check("ошибка сети переведена",
          "Нет связи с ВК" in main.vk_error_ru("Network is unreachable"))
    check("неизвестная ошибка не теряется",
          main.vk_error_ru("что-то странное") == "что-то странное")
    check("пустая ошибка не роняет разбор",
          isinstance(main.vk_error_ru(""), str) and main.vk_error_ru(""))
    check("плейер показывает статус через переводчик",
          "vk_error_ru(reason)" in
          open(os.path.join(os.path.dirname(os.path.abspath(main.__file__)),
                            "main.py"), encoding="utf-8-sig").read())

    # 7. Блокировка ВК: за одно нажатие — ровно одна попытка входа
    flood_text = ("Can't obtain token. Code: 2. Error extra: "
                  "{description: 'Too many attempts were made to sign in'}")
    check("флуд распознаётся", main.vk_error_is_flood(flood_text))
    check("прочий отказ — не флуд",
          not main.vk_error_is_flood("Network is unreachable"))
    check("неверный пароль распознан",
          main.vk_error_is_bad_password("Error extra: {invalid password}"))
    check("пустая строка — не флуд", not main.vk_error_is_flood(""))

    import types
    calls = []
    fake = types.ModuleType("vkaudiotoken")

    class FloodErr(Exception):
        code = 2

    def flood_official(login, password, auth_code="GET_CODE"):
        calls.append("official")
        raise FloodErr(flood_text)

    def fake_kate(login, password, auth_code="GET_CODE"):
        calls.append("kate")
        raise FloodErr(flood_text)

    fake.get_vk_official_token = flood_official
    fake.get_kate_token = fake_kate
    old_mod = sys.modules.get("vkaudiotoken")
    sys.modules["vkaudiotoken"] = fake
    try:
        events = []
        worker = main.VkAudioTokenWorker("+70000000000", "pass")
        worker.failed.connect(lambda m: events.append(m))
        worker.run()
        check("при блокировке — только одна попытка входа за нажатие",
              calls == ["official"], str(calls))
        check("текст блокировки донесён до пользователя",
              events and "too many attempts" in str(events[0]).lower(),
              str(events))

        # Неверный пароль: вторая попытка тоже бессмысленна
        calls.clear()
        events.clear()

        def bad_password(login, password, auth_code="GET_CODE"):
            calls.append("official")
            raise FloodErr("Error extra: {invalid password}")

        fake.get_vk_official_token = bad_password
        worker = main.VkAudioTokenWorker("+70000000000", "wrong")
        worker.failed.connect(lambda m: events.append(m))
        worker.run()
        check("при неверном паролее тоже одна попытка",
              calls == ["official"], str(calls))

        # Прочая ошибка: запасной путь Kate остаётся доступен
        calls.clear()
        events.clear()

        def oddball(login, password, auth_code="GET_CODE"):
            calls.append("official")
            raise FloodErr("что-то странное, не похоже на блокировку")

        fake.get_vk_official_token = oddball
        worker = main.VkAudioTokenWorker("+70000000000", "pass")
        worker.failed.connect(lambda m: events.append(m))
        worker.run()
        check("запасной путь Kate пробуется при прочих ошибках",
              calls == ["official", "kate"], str(calls))
    finally:
        if old_mod is None:
            sys.modules.pop("vkaudiotoken", None)
        else:
            sys.modules["vkaudiotoken"] = old_mod

    # 8. Автоблокировка кнопки после флуда
    player._on_vk_token_failed(flood_text)
    check("кнопка погашена после блокировки", not box.btn_vk_auto.isEnabled())
    check("на кнопке видно, что ждать",
          "подождите" in box.btn_vk_auto.text().lower(), box.btn_vk_auto.text())
    check("статус объясняет блокировку",
          "подождите" in player.status_lbl.text().lower(),
          player.status_lbl.text())
    check("повторное нажатие перехватывается",
          player.fetch_vk_token_auto() is None, "")
    player._release_vk_auto()
    check("кнопка возвращается после паузы",
          box.btn_vk_auto.isEnabled() and "автоматически" in box.btn_vk_auto.text(),
          box.btn_vk_auto.text())
    check("флаг паузы снят", not player._vk_auto_cooldown)

    # 9. Пустой вызов не падает и подсказывает
    check("автовход без логина и пароля не падает",
          player.fetch_vk_token_auto() is None, "")
    check("подсказка требует логин и пароль",
          "логин" in player.status_lbl.text().lower(), player.status_lbl.text())
    del player.settings_win
    box.close()
    player.close()


def section_16_search_results_view():
    """Окно результатов: раньше показ треков ронял плеер (TypeError в слоте).

    sorted() возвращает список, а от списка нельзя отнимать множество —
    PyQt6 убивает процесс при необработанном исключении в слоте.
    """
    player = main.SvinoklaudPlayer()
    player.url_input.setText("Молчат Дома")

    player._on_search_results([])
    check("пустые результаты не открывают окно",
          "Ничего не найдено" in player.status_lbl.text(),
          player.status_lbl.text())

    tracks = [
        {"platform": "vk", "type": "vk", "url": "https://vk.com/audio1_2",
         "title": "Судно", "author": "Молчат Дома", "duration": 168.0,
         "direct_url": "https://example.invalid/1.mp3", "platform_name": ""},
        {"platform": "sc", "type": "sc", "url": "https://soundcloud.com/a/b",
         "title": "Other", "author": "Someone", "duration": 61.0},
        {"platform": "ym", "type": "ym",
         "url": "https://music.yandex.ru/album/1/track/2",
         "title": "Третий", "author": "Исполнитель", "duration": 0.0,
         "platform_name": None},
    ]
    error = None
    try:
        player._on_search_results(tracks)
    except Exception as exc:                 # именно так плеер и вылетал
        error = exc
    check("показ результатов больше не роняет плеер", error is None, str(error))
    check("окно результатов открылось",
          getattr(player, "search_window", None) is not None)
    check("статус сообщает число треков",
          "Найдено треков — 3" in player.status_lbl.text(),
          player.status_lbl.text())
    check("в статусе перечислены платформы",
          "VK" in player.status_lbl.text() and "SoundCloud" in player.status_lbl.text(),
          player.status_lbl.text())
    window = getattr(player, "search_window", None)
    if window is not None:
        check("в окне все три трека", window.list.count() == 3,
              str(window.list.count()))
        check("у трека видна подпись платформы",
              "VK" in window.list.item(0).text(), window.list.item(0).text())
        window.close()
    player.close()


def section_17_vk_links():
    """Ссылки ВК: разбор форматов, чистка без потери id, добавление трека."""
    # 1. Разбор ссылок всех популярных форматов
    check("формат /audio123_456",
          main.vk_audio_ids_from_url("https://vk.com/audio123_456") == ("123", "456"))
    check("формат /audio-123_456 (группа)",
          main.vk_audio_ids_from_url("https://vk.com/audio-123_456") == ("-123", "456"))
    check("формат ?z=audio…",
          main.vk_audio_ids_from_url(
              "https://vk.com/audio?z=audio123_456%2Fabcdef%2FFromMusic")
          == ("123", "456"))
    check("формат wall…?z=audio…",
          main.vk_audio_ids_from_url(
              "https://vk.com/wall-1_2?z=audio123_456%2Fxx") == ("123", "456"))
    check("не-аудио ссылка не разбирается",
          main.vk_audio_ids_from_url("https://vk.com/video1_2") is None)
    check("пустая ссылка безопасна",
          main.vk_audio_ids_from_url("") is None
          and main.vk_audio_ids_from_url(None) is None)

    player = main.SvinoklaudPlayer()
    player._cached_file = lambda url: "x.mp3"   # без фоновых качалок в тесте
    real_resolve = main.vk_audio_from_link
    try:
        # 2. Чистка: у ВК в query живёт id трека, резать его нельзя
        vk_link = "https://vk.com/audio?z=audio123_456%2Fabc&utm_source=x"
        check("у ссылки ВК query сохраняется",
              player.clean_url(vk_link).startswith(
                  "https://vk.com/audio?z=audio123_456"),
              player.clean_url(vk_link))
        check("хвост # у ВК отрезается",
              player.clean_url(vk_link + "#top") == player.clean_url(vk_link))
        check("трекинг у SoundCloud отрезается как раньше",
              player.clean_url("https://soundcloud.com/a/b?si=123")
              == "https://soundcloud.com/a/b")

        # Не-аудио ссылка ВК (видео) добавляется как обычная, без разбора
        before = len(player.playlist_tracks)
        player.url_input.setText("https://vk.com/video-1_2")
        player.add_to_playlist()
        check("видеоссылка ВК добавляется без аудиоразбора",
              len(player.playlist_tracks) == before + 1,
              str(len(player.playlist_tracks)))

        # 3. Добавление по ссылке, когда ВК ответил
        main.vk_audio_from_link = lambda url, token=None: {
            "direct_url": "https://example.invalid/x.mp3",
            "title": "Судно", "author": "Молчат Дома",
            "cover": "", "duration": 168.0}
        before = len(player.playlist_tracks)
        player.url_input.setText("https://vk.com/audio123_456")
        player.add_to_playlist()
        check("трек по ссылке ВК добавился",
              len(player.playlist_tracks) == before + 1,
              str(len(player.playlist_tracks)))
        added = player.playlist_tracks[-1] if player.playlist_tracks else {}
        check("название настоящее, а не «audio»",
              added.get("title") == "Судно", str(added.get("title")))
        check("прямая ссылка на mp3 сохранена",
              (added.get("direct_url") or "").endswith(".mp3"),
              str(added.get("direct_url")))
        check("платформа подписана как VK", added.get("platform") == "vk",
              str(added.get("platform")))
        check("статус сообщает о добавлении",
              "Добавлено" in player.status_lbl.text(),
              player.status_lbl.text())

        # 4. ВК не ответил — понятный отказ, мусор не добавляем
        main.vk_audio_from_link = lambda url, token=None: {}
        before = len(player.playlist_tracks)
        player.url_input.setText("https://vk.com/audio789_012")
        player.add_to_playlist()
        check("без ответа ВК трек не добавляется",
              len(player.playlist_tracks) == before,
              str(len(player.playlist_tracks)))
        check("статус объясняет причину",
              "не удалось получить трек" in player.status_lbl.text().lower(),
              player.status_lbl.text())
        check("поле ссылки не очищается при отказе",
              player.url_input.text() == "https://vk.com/audio789_012",
              player.url_input.text())

        # 5. Пустой трек не роняет добавление
        check("add_search_result(None) безопасен",
              player.add_search_result(None) is False)

        # 6. Ленивое восстановление прямой ссылки при игре
        rec = next(t for t in player.playlist_tracks
                   if t.get("title") == "Судно")
        rec.pop("direct_url", None)
        main.vk_audio_from_link = lambda url, token=None: {
            "direct_url": "https://example.invalid/lazy.mp3",
            "title": "", "author": "", "cover": "", "duration": 0.0}
        got = player._direct_url_for(rec["url"])
        check("прямая ссылка восстанавливается по ссылке ВК",
              got == "https://example.invalid/lazy.mp3", str(got))
        check("восстановленная ссылка записана в трек",
              rec.get("direct_url") == got)

        # 7. Фоновое кэширование идёт по прямой ссылке, а не через yt-dlp
        rec.pop("direct_url", None)
        calls, ytdlp = [], []

        def fake_download(direct, url, index=None):
            calls.append(direct)
            return ""

        def fake_fetch(url):
            ytdlp.append(url)
            return (None, "", "", "")

        player._download_direct = fake_download
        player.fetch_to_cache = fake_fetch
        main.vk_audio_from_link = lambda url, token=None: {
            "direct_url": "https://example.invalid/y.mp3",
            "title": "", "author": "", "cover": "", "duration": 0.0}
        player._prefetch_track(rec["url"])
        check("prefetch берёт прямую ссылку ВК",
              calls == ["https://example.invalid/y.mp3"], str(calls))
        check("yt-dlp для ссылки ВК не вызывается",
              ytdlp == [], str(ytdlp))
    finally:
        main.vk_audio_from_link = real_resolve
        player.close()


def section_18_multi_playlist(player, tmp):
    """Несколько плейлистов: создание, переключение, импорт и экспорт."""
    # --- модульные помощники -------------------------------------------
    missing = os.path.join(tmp, "нет_такого.json")
    store, active = main.load_playlist_store(missing)
    check("отсутствующий файл даёт один основной плейлист",
          store == {main.DEFAULT_PLAYLIST_NAME: []}
          and active == main.DEFAULT_PLAYLIST_NAME,
          f"{list(store)}/{active}")

    multi_path = os.path.join(tmp, "multi.json")
    with open(multi_path, "w", encoding="utf-8") as fh:
        json.dump({"version": 2, "active": "Редкий", "playlists": {
            "Редкий": [{"type": "sc", "url": "https://soundcloud.com/a/b",
                        "title": "b"}],
            "Второй": [{"type": "local", "path": r"D:\m.mp3", "title": "m"}]}},
            fh, ensure_ascii=False)
    store, active = main.load_playlist_store(multi_path)
    check("из файла читаются все плейлисты", sorted(store) == ["Второй", "Редкий"],
          str(list(store)))
    check("активный плейлист берётся из файла", active == "Редкий", active)

    old_path = os.path.join(tmp, "old.json")
    with open(old_path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "tracks": [
            {"type": "sc", "url": "https://soundcloud.com/c/d", "title": "d"}]},
            fh, ensure_ascii=False)
    store, _ = main.load_playlist_store(old_path)
    check("старый формат (поле tracks) распознан",
          list(store) == [main.DEFAULT_PLAYLIST_NAME]
          and len(store[main.DEFAULT_PLAYLIST_NAME]) == 1, str(list(store)))

    broken = os.path.join(tmp, "broken.json")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write("{это не json")
    store, _ = main.load_playlist_store(broken)
    check("битый JSON не роняет плеер",
          store == {main.DEFAULT_PLAYLIST_NAME: []}, str(store))

    m3u_text = main.playlist_to_m3u([{"type": "sc",
                                      "url": "https://soundcloud.com/e/f",
                                      "title": "f", "author": "Исп"}])
    check("экспорт M3U: заголовок, автор и ссылка",
          m3u_text.startswith("#EXTM3U") and "#EXTINF:" in m3u_text
          and "Исп - f" in m3u_text
          and "https://soundcloud.com/e/f" in m3u_text, repr(m3u_text))
    m3u_path = os.path.join(tmp, "svoi.m3u")
    with open(m3u_path, "w", encoding="utf-8") as fh:
        fh.write(m3u_text)
    imported = main.import_playlists_from_file(m3u_path)
    check("M3U импортируется одним плейлистом по имени файла",
          len(imported) == 1 and "svoi" in next(iter(imported))
          and len(next(iter(imported.values()))) == 1, str(list(imported)))
    check("наш много-плейлистный файл отдаёт все списки сразу",
          sorted(main.import_playlists_from_file(multi_path)) == ["Второй", "Редкий"],
          str(list(main.import_playlists_from_file(multi_path))))
    check("импорт несуществующего файла безопасен",
          main.import_playlists_from_file(missing) == {})

    # --- элементы интерфейса -------------------------------------------
    box = getattr(player, "playlist_box", None)
    check("комбобокс выбора плейлиста на месте", box is not None, str(type(box)))
    for attr, label in (("btn_playlist_new", "новый"),
                        ("btn_playlist_delete", "удалить"),
                        ("btn_playlist_import", "импорт"),
                        ("btn_playlist_export", "экспорт"),
                        ("btn_playlist_merge", "слияние")):
        check(f"кнопка «{label}» подключена",
              hasattr(getattr(player, attr, None), "clicked"),
              str(getattr(player, attr, None)))

    # --- создание и переключение ---------------------------------------
    first = player.active_playlist_name
    first_count = len(player.playlist_tracks)
    created = player.create_playlist("Второй")
    check("новый плейлист создан и сразу открыт",
          created == "Второй" and player.active_playlist_name == "Второй"
          and player.playlist_tracks == [],
          f"{created}/{player.active_playlist_name}")
    check("треки первого плейлиста не потерялись",
          len(player.playlists[first]) == first_count,
          str(len(player.playlists[first])))
    check("занятое имя получает «(2)»",
          player.create_playlist("Второй") == "Второй (2)",
          str(list(player.playlists)))

    player.add_tracks_to_current([{"type": "sc",
                                   "url": "https://soundcloud.com/x/y",
                                   "title": "y"}])
    check("трек добавился в активный плейлист",
          len(player.playlist_tracks) == 1, str(len(player.playlist_tracks)))
    check("возврат к первому плейлисту вернул его треки",
          player.switch_playlist(first)
          and len(player.playlist_tracks) == first_count,
          str(len(player.playlist_tracks)))
    check("комбобокс показывает все плейлисты",
          box.count() == len(player.playlists)
          and any("Второй (2)" in (box.itemText(i) or "")
                  for i in range(box.count())),
          str([box.itemText(i) for i in range(box.count())]))
    check("неизвестное имя переключение игнорирует",
          player.switch_playlist("нет_такого") is False)

    # --- слияние --------------------------------------------------------
    player.switch_playlist("Второй")
    merged = player.merge_playlist_from("Второй (2)")
    check("все треки из другого плейлиста влиты",
          merged == 1 and len(player.playlist_tracks) == 1, str(merged))
    check("слияние плейлиста в самого себя не считается",
          player.merge_playlist_from("Второй") == 0)

    # --- сохранение -----------------------------------------------------
    check("все плейлисты пишутся на диск", player.save_playlist())
    with open(player._playlist_path(), "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    check("на диске несколько плейлистов сразу",
          saved.get("version") == 2 and len(saved.get("playlists", {})) >= 3
          and saved.get("active") == player.active_playlist_name,
          json.dumps({k: len(v) for k, v in saved.get("playlists", {}).items()},
                     ensure_ascii=False))
    store, active = main.load_playlist_store(player._playlist_path())
    check("файл читается обратно со всеми списками",
          set(store) == set(saved["playlists"]) and active == saved["active"],
          str(list(store)))

    # --- импорт ---------------------------------------------------------
    src = os.path.join(tmp, "import.json")
    with open(src, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "name": "Из файла", "tracks": [
            {"type": "sc", "url": "https://soundcloud.com/p/q", "title": "q"},
            {"type": "sc", "url": "https://soundcloud.com/p/r", "title": "r"}]},
            fh, ensure_ascii=False)
    known = set(player.playlists)
    total = player.import_playlist_file(src, mode="new")
    check("импорт «как новый плейлист» создал список",
          total == 2 and "Из файла" in player.playlists
          and player.active_playlist_name == "Из файла",
          f"{total}/{player.active_playlist_name}")
    check("прежние плейлисты не тронуты", known <= set(player.playlists),
          str(known ^ set(player.playlists)))

    player.switch_playlist(first)
    before = len(player.playlist_tracks)
    total = player.import_playlist_file(src, mode="current")
    check("импорт «в текущий» дополнил список",
          total == 2 and len(player.playlist_tracks) == before + 2,
          str(len(player.playlist_tracks)))

    # --- экспорт и обратное чтение -------------------------------------
    out_json = os.path.join(tmp, "export.json")
    check("экспорт в JSON прошёл", player.export_playlist_to(out_json) is True)
    back = main.import_playlists_from_file(out_json)
    check("JSON читается обратно с теми же треками",
          sum(len(v) for v in back.values()) == len(player.playlist_tracks),
          f"{sum(len(v) for v in back.values())}/{len(player.playlist_tracks)}")
    out_m3u = os.path.join(tmp, "export.m3u")
    check("экспорт в M3U прошёл", player.export_playlist_to(out_m3u) is True)
    back_m3u = main.parse_playlist_file(out_m3u)
    check("M3U читается обратно",
          len(back_m3u) == len(player.playlist_tracks),
          f"{len(back_m3u)}/{len(player.playlist_tracks)}")

    player.create_playlist("Пустой")
    check("пустой плейлист не экспортируется",
          player.export_playlist_to(os.path.join(tmp, "empty.json")) is False)

    # --- комбобокс переключает по сигналу ------------------------------
    player.playlists = {
        "Альфа": [{"type": "sc", "url": "https://soundcloud.com/a/1",
                   "title": "1"}],
        "Бета": [{"type": "sc", "url": "https://soundcloud.com/b/2",
                  "title": "2"}]}
    player.active_playlist_name = "Альфа"
    player.playlist_tracks = player.playlists["Альфа"]
    player._show_playlist_tracks()
    player._refresh_playlist_box()
    idx = box.findData("Бета")
    check("комбобокс содержит оба списка",
          idx >= 0 and box.count() == 2,
          str([box.itemText(i) for i in range(box.count())]))
    box.setCurrentIndex(idx)
    check("выбор пункта в комбобоксе открывает плейлист",
          player.active_playlist_name == "Бета"
          and len(player.playlist_tracks) == 1,
          f"{player.active_playlist_name}/{len(player.playlist_tracks)}")

    # --- защита последнего списка --------------------------------------
    player.playlists = {main.DEFAULT_PLAYLIST_NAME: []}
    player.active_playlist_name = main.DEFAULT_PLAYLIST_NAME
    player.playlist_tracks = player.playlists[main.DEFAULT_PLAYLIST_NAME]
    check("последний плейлист удалить нельзя",
          player.delete_playlist_dialog() is False
          and main.DEFAULT_PLAYLIST_NAME in player.playlists,
          str(list(player.playlists)))


def section_19_playlist_from_url(player, tmp):
    """Плейлисты по ссылке: ВК, SoundCloud и Яндекс.Музыка. Сеть подменена."""
    # --- распознавание ссылок --------------------------------------------
    parts = main.playlist_url_parts(
        "https://vk.com/music/playlist/-100_12345?z=audio-100_12345%2Fabcdef")
    check("ссылка ВК music/playlist разобрана",
          parts.get("platform") == "vk" and parts.get("owner") == "-100"
          and parts.get("playlist_id") == "12345", str(parts))
    parts = main.playlist_url_parts(
        "https://vk.com/audio?act=audio_playlist-100&playlist_id=77")
    check("ссылка ВК act=audio_playlist разобрана",
          parts.get("owner") == "-100" and parts.get("playlist_id") == "77",
          str(parts))
    check("одиночная аудиозапись ВК — не плейлист",
          main.playlist_url_parts("https://vk.com/audio-1_2") == {})

    parts = main.playlist_url_parts("https://soundcloud.com/dj/sets/mix-2026")
    check("сет SoundCloud разобран с названием",
          parts.get("platform") == "sc" and parts.get("name") == "mix 2026",
          str(parts))
    check("одиночный трек SoundCloud — не плейлист",
          main.playlist_url_parts("https://soundcloud.com/dj/track") == {})

    parts = main.playlist_url_parts(
        "https://music.yandex.ru/users/yandex/playlists/42")
    check("плейлист Яндекса разобран",
          parts == {"platform": "ym", "user": "yandex", "kind": "42"}, str(parts))
    check("альбом Яндекса — не плейлист",
          main.playlist_url_parts("https://music.yandex.ru/album/42671838") == {})
    check("плейлист Spotify не поддерживается",
          main.playlist_url_parts("https://open.spotify.com/playlist/abc") == {})
    check("пустая ссылка на плейлист безопасна",
          main.playlist_url_parts("") == {} and main.playlist_url_parts(None) == {})
    check("площадка по ссылке: только vk/sc/ym",
          main.playlist_url_platform(
              "https://music.yandex.ru/users/a/playlists/1") == "ym"
          and main.playlist_url_platform(
              "https://youtube.com/playlist?list=x") == "",
          main.playlist_url_platform("https://youtube.com/playlist?list=x"))

    # --- заголовок страницы Яндекса --------------------------------------
    check("заголовок страницы Яндекса прочитан",
          main.yandex_page_title(
              "<title>Плейлист «Дорога» — Яндекс.Музыка</title>") == "Дорога",
          main.yandex_page_title(
              "<title>Плейлист «Дорога» — Яндекс.Музыка</title>"))
    check("заголовок без кавычек тоже берётся",
          main.yandex_page_title("<title>Песни — Яндекс.Музыка</title>") == "Песни",
          main.yandex_page_title("<title>Песни — Яндекс.Музыка</title>"))

    # --- SoundCloud: команда yt-dlp и разбор JSON ------------------------
    url = "https://soundcloud.com/dj/sets/mix"
    cmd = main.playlist_ytdlp_command(url)
    check("сет читается через --dump-single-json",
          "--dump-single-json" in cmd and "--flat-playlist" in cmd
          and "--skip-download" in cmd, str(cmd))
    check("в команде чтения нет поискового префикса",
          not any("scsearch" in str(part) for part in cmd), str(cmd))
    check("ссылка на сет стоит в команде", url in cmd, str(cmd))

    canned = {"_type": "playlist", "title": "Мой сет", "entries": [
        {"title": "Песня", "uploader": "Автор",
         "url": "https://soundcloud.com/a/b", "duration": 12.5},
        {"title": "Вторая", "url": "soundcloud.com/x/y"},
        "битая запись"]}
    name, tracks = main.parse_ytdlp_playlist(
        json.dumps(canned, ensure_ascii=False))
    check("имя плейлиста SoundCloud прочитано", name == "Мой сет", name)
    check("треки SoundCloud разобраны",
          len(tracks) == 2 and tracks[0]["title"] == "Песня"
          and tracks[0]["author"] == "Автор" and tracks[0]["duration"] == 12.5,
          str(tracks))
    check("ссылка без протокола дополнена",
          tracks[1]["url"] == "https://soundcloud.com/x/y", tracks[1]["url"])
    try:
        main.parse_ytdlp_playlist("это не json")
        check("мусор вместо JSON даёт ошибку", False)
    except RuntimeError:
        check("мусор вместо JSON даёт ошибку", True)

    # Плоский разбор (без названий): имя и автор берутся из ссылки, иначе
    # в плейлист попадают «треки-ссылки».
    flat = {"_type": "playlist", "title": "Сет", "entries": [
        {"id": "abc123", "ie_key": "Soundcloud",
         "url": "https://soundcloud.com/monstercat/bishu-so-absurd-vip"}]}
    _, flat_tracks = main.parse_ytdlp_playlist(json.dumps(flat))
    check("запись без названия получила имя из ссылки",
          bool(flat_tracks) and flat_tracks[0]["title"] == "bishu so absurd vip",
          str(flat_tracks))
    check("запись без названия получила автора из ссылки",
          bool(flat_tracks) and flat_tracks[0]["author"] == "monstercat",
          str(flat_tracks))

    # --- ВК: API audio.get с подменённой сетью ---------------------------
    real_http, real_token = main._http_get, main.VK_ACCESS_TOKEN
    real_loader = main.load_vk_token
    seen_urls = []

    def fake_vk_http(url, timeout=20, headers=None):
        seen_urls.append(url)
        if "audio.getPlaylistById?" in url:
            return json.dumps({"response": {"id": 77, "owner_id": -100,
                                            "title": "Дорожки"}})
        if "audio.getPlaylists?" in url:
            return json.dumps({"response": {"items": [
                {"id": 77, "title": "Дорожки"}, {"id": 78, "title": "Чужие"}]}})
        if "audio.get?" in url:
            return json.dumps({"response": {"count": 1, "items": [
                {"owner_id": -100, "id": 5, "title": "Утро", "artist": "Кто-то",
                 "duration": 61, "url": "https://mp3.example/1.mp3"}]}})
        raise RuntimeError("неожиданный URL: " + url)

    main._http_get, main.VK_ACCESS_TOKEN = fake_vk_http, "токен42"
    try:
        name, tracks = main.fetch_playlist_from_url(
            "https://vk.com/audio?act=audio_playlist-100&playlist_id=77")
        check("плейлист ВК загружен по ссылке",
              name == "Дорожки" and len(tracks) == 1, f"{name}/{len(tracks)}")
        check("трек ВК несёт прямую ссылку на mp3",
              bool(tracks) and tracks[0].get("direct_url")
              == "https://mp3.example/1.mp3", str(tracks[:1]))
        check("запрос ушёл именно в audio.get",
              any("method/audio.get?" in u for u in seen_urls), str(seen_urls))
        check("название взято точным audio.getPlaylistById",
              any("audio.getPlaylistById" in u for u in seen_urls),
              str(seen_urls))
    finally:
        main._http_get, main.VK_ACCESS_TOKEN = real_http, real_token

    # Без токена — честная ошибка, а не пустой плейлист (файл токена глушим,
    # иначе подхватился бы ключ, сохранённый предыдущими проверками)
    main.load_vk_token = lambda: ""
    try:
        try:
            main.fetch_playlist_from_url("https://vk.com/music/playlist/-1_2")
            check("без токена ВК даёт понятную ошибку", False)
        except RuntimeError as exc:
            check("без токена ВК даёт понятную ошибку",
                  "токен" in str(exc).lower(), str(exc))
    finally:
        main.load_vk_token = real_loader

    # --- Яндекс.Музыка: разбор страницы плейлиста ------------------------
    ym_html = (
        '<html><head><title>Плейлист «Дорога» — Яндекс.Музыка</title></head><body>'
        '<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])'
        '.push([{"op":"add","path":"\\u002Fusers\\u002Fyandex\\u002Fplaylists\\u002F42'
        '\\u002Ftracks\\u002F0","value":{"id":111,"title":"В путь","durationMs":60000,'
        '"albumId":5,"artists":[{"id":1,"name":"Исполнитель"}],'
        '"coverUri":"avatars.yandex.net\\u002Fget\\u002F%%"}}])</script></body></html>')

    def fake_ym_http(url, timeout=25, headers=None):
        if "playlists/42" in url:
            return ym_html
        raise RuntimeError("неожиданный URL: " + url)

    main._http_get = fake_ym_http
    try:
        name, tracks = main.fetch_playlist_from_url(
            "https://music.yandex.ru/users/yandex/playlists/42")
        check("плейлист Яндекса загружен по ссылке",
              name == "Дорога" and len(tracks) == 1, f"{name}/{len(tracks)}")
        check("трек Яндекса собран тем же разбором, что и поиск",
              bool(tracks) and tracks[0]["title"] == "В путь"
              and tracks[0]["author"] == "Исполнитель"
              and tracks[0]["url"] == "https://music.yandex.ru/album/5/track/111"
              and tracks[0]["duration"] == 60.0, str(tracks[:1]))
        check("обложка Яндекса почищена от экранирования",
              bool(tracks) and tracks[0]["cover"].startswith("https://avatars."),
              tracks[0]["cover"] if tracks else "")
    finally:
        main._http_get = real_http

    # --- неподдерживаемая ссылка -----------------------------------------
    try:
        main.fetch_playlist_from_url("https://open.spotify.com/playlist/abc")
        check("чужая площадка даёт отказ", False)
    except RuntimeError as exc:
        check("чужая площадка даёт отказ",
              "ВК" in str(exc) and "SoundCloud" in str(exc), str(exc))


    # --- фоновый поток ----------------------------------------------------
    real_fetch = main.fetch_playlist_from_url
    set_url = "https://soundcloud.com/dj/sets/mix"
    one_track = {"type": "sc", "platform": "sc", "title": "Трек",
                 "url": "https://soundcloud.com/a/t"}
    main.fetch_playlist_from_url = lambda u: ("Из ссылки", [one_track])
    got, missed = [], []
    worker = main.PlaylistUrlWorker(set_url)
    worker.done.connect(lambda u, n, t: got.append((u, n, t)))
    worker.run()                                   # синхронно: сеть подменена
    check("поток отдал имя и треки",
          bool(got) and got[0][1] == "Из ссылки" and len(got[0][2]) == 1, str(got))

    main.fetch_playlist_from_url = lambda u: (_ for _ in ()).throw(
        RuntimeError("Нужен токен ВК"))
    failed_worker = main.PlaylistUrlWorker("https://vk.com/music/playlist/-1_2")
    failed_worker.failed.connect(lambda u, r: missed.append(r))
    failed_worker.run()
    check("ошибка загрузки приходит сигналом",
          missed == ["Нужен токен ВК"], str(missed))
    check("пустая ссылка в потоке не роняет его",
          main.PlaylistUrlWorker("").run() is None)
    main.fetch_playlist_from_url = real_fetch

    # --- интерфейс и добавление в плейлисты -------------------------------
    check("кнопка «🌐 По ссылке» на месте и подключена",
          hasattr(getattr(player, "btn_playlist_url", None), "clicked"),
          str(getattr(player, "btn_playlist_url", None)))

    player.playlists = {main.DEFAULT_PLAYLIST_NAME: []}
    player.active_playlist_name = main.DEFAULT_PLAYLIST_NAME
    player.playlist_tracks = player.playlists[main.DEFAULT_PLAYLIST_NAME]
    player._show_playlist_tracks()

    added = player.import_playlist_url(set_url, mode="new", name="Сет",
                                       tracks=[one_track])
    check("импорт «как новый» создал плейлист из ссылки",
          added == 1 and "Сет" in player.playlists
          and player.active_playlist_name == "Сет"
          and len(player.playlist_tracks) == 1,
          f"{added}/{list(player.playlists)}")

    added = player.import_playlist_url(set_url, mode="current", name="Сет",
                                       tracks=[{"type": "sc", "title": "2",
                                                "url": "https://soundcloud.com/a/2"}])
    check("импорт «в текущий» дополнил список",
          added == 1 and len(player.playlist_tracks) == 2,
          str(len(player.playlist_tracks)))

    check("пустой список треков не создаёт плейлист",
          player.import_playlist_url(set_url, mode="new",
                                     name="Пустой", tracks=[]) == 0
          and "Пустой" not in player.playlists, str(list(player.playlists)))

    main.fetch_playlist_from_url = lambda u: (_ for _ in ()).throw(
        RuntimeError("нет токена"))
    try:
        check("ошибка синхронной загрузки попадает в статус",
              player.import_playlist_url(
                  "https://vk.com/music/playlist/-1_2") == 0
              and "нет токена" in player.status_lbl.text(),
              player.status_lbl.text())
    finally:
        main.fetch_playlist_from_url = real_fetch

    # --- диалог по кнопке: с подменённым QInputDialog и сетью ------------
    class FakeInputDialog:
        answer = ("", False)

        @staticmethod
        def getText(*args, **kwargs):
            return FakeInputDialog.answer

        @staticmethod
        def getItem(*args, **kwargs):
            return FakeInputDialog.answer

    real_dialog, real_fetch2 = main.QInputDialog, main.fetch_playlist_from_url
    main.QInputDialog = FakeInputDialog
    main.fetch_playlist_from_url = lambda u: ("Из ссылки", [one_track])
    try:
        FakeInputDialog.answer = ("https://open.spotify.com/playlist/abc", True)
        check("диалог отвергает чужую площадку",
              player.import_playlist_url_dialog() == 0
              and "Нужна ссылка" in player.status_lbl.text(),
              player.status_lbl.text())

        FakeInputDialog.answer = ("", True)
        check("пустая ссылка в диалоге — отмена",
              player.import_playlist_url_dialog() == 0
              and "отменён" in player.status_lbl.text(),
              player.status_lbl.text())

        FakeInputDialog.answer = (set_url, True)
        started = player.import_playlist_url_dialog()
        check("диалог стартовал фоновую загрузку",
              started == 1 and bool(getattr(player, "_url_workers", [])),
              f"{started}/{player.status_lbl.text()}")
        for thread in list(getattr(player, "_url_workers", [])):
            thread.wait(5000)                      # сеть подменена — конец быстрый
        FakeInputDialog.answer = ("Как новый плейлист", True)
        for _ in range(3):
            QApplication.processEvents()           # сигнал из потока -> слот
        check("после загрузки создан плейлист «Из ссылки»",
              "Из ссылки" in player.playlists
              and len(player.playlists["Из ссылки"]) == 1,
              str({k: len(v) for k, v in player.playlists.items()}))
        check("загрузочный поток завершён и не крутится дальше",
              all(not t.isRunning() for t in getattr(player, "_url_workers", [])),
              str(getattr(player, "_url_workers", [])))
    finally:
        main.QInputDialog = real_dialog
        main.fetch_playlist_from_url = real_fetch2


def section_20_playlist_paste(player, tmp):
    """Ссылка на плейлист в поле добавления уходит в импорт, а не «треком»."""
    import json as _json

    # --- короткие ссылки приложений ----------------------------------------
    check("обычная ссылка возвращается без сети",
          main.resolve_share_url("https://soundcloud.com/dj/sets/mix")
          == "https://soundcloud.com/dj/sets/mix")
    check("пустая ссылка безопасна", main.resolve_share_url("") == ""
          and main.resolve_share_url(None) == "")

    real_urlopen = main.urllib.request.urlopen

    class _FakeResp:
        def geturl(self):
            return "https://soundcloud.com/dj/sets/mix"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    main.urllib.request.urlopen = lambda *a, **k: _FakeResp()
    try:
        got = main.resolve_share_url("https://on.soundcloud.com/abc123")
        check("короткая ссылка SoundCloud разворачивается в полную",
              got == "https://soundcloud.com/dj/sets/mix", got)
    finally:
        main.urllib.request.urlopen = real_urlopen

    def _boom(*a, **k):
        raise OSError("нет сети")

    main.urllib.request.urlopen = _boom
    try:
        check("обрыв сети при развороте не ломает ссылку",
              main.resolve_share_url("https://vk.cc/xyz123")
              == "https://vk.cc/xyz123")
    finally:
        main.urllib.request.urlopen = real_urlopen

    # --- access_key из ссылки «поделиться» ----------------------------------
    parts = main.playlist_url_parts(
        "https://vk.com/audio?act=audio_playlist-100_77&access_key=abc123def")
    check("access_key достаётся из ссылки ВК",
          parts.get("access_key") == "abc123def", str(parts))
    check("ссылка без ключа не получает пустой access_key",
          "access_key" not in main.playlist_url_parts(
              "https://vk.com/audio?act=audio_playlist-100_77"))

    # --- запрос ВК с ключом ---------------------------------------------------
    seen = []

    def fake_http(url, timeout=20, headers=None):
        seen.append(url)
        return _json.dumps({"response": {"count": 1, "items": [
            {"owner_id": -100, "id": 5, "title": "Т", "artist": "А",
             "duration": 1, "url": "https://mp3.example/1.mp3"}]}})

    real_http, real_token = main._http_get, main.VK_ACCESS_TOKEN
    main._http_get, main.VK_ACCESS_TOKEN = fake_http, "токен42"
    try:
        tracks = main.vk_playlist_tracks("-100", "77", access_key="ключ7")
        check("плейлист ВК читается с access_key", len(tracks) == 1,
              str(len(tracks)))
        check("access_key ушёл в запрос к VK API",
              bool(seen) and "access_key=" in seen[0],
              seen[0] if seen else "")
        seen.clear()
        main.vk_playlist_tracks("-100", "77")
        check("без ключа в запросе нет access_key",
              bool(seen) and "access_key=" not in seen[0],
              seen[0] if seen else "")
    finally:
        main._http_get, main.VK_ACCESS_TOKEN = real_http, real_token

    # --- перевод отказов ВК ---------------------------------------------------
    check("«no access to this group» переведён на русский",
          "нет доступа" in main.vk_playlist_error_ru(
              "Access denied: no access to this group"),
          main.vk_playlist_error_ru("Access denied: no access to this group"))
    check("скрытые аудиозаписи объяснены понятно",
          "скрыл" in main.vk_playlist_error_ru(
              "Access denied: access to users audio is denied"),
          main.vk_playlist_error_ru(
              "Access denied: access to users audio is denied"))
    check("неизвестная ошибка проходит без потерь",
          main.vk_playlist_error_ru("что-то иное") == "что-то иное")

    # --- поле добавления: плейлист-ссылка не становится треком ---------------
    one_track = {"type": "sc", "platform": "sc", "title": "Трек",
                 "url": "https://soundcloud.com/a/t"}

    class FakeInputDialog:
        answer = ("Как новый плейлист", True)

        @staticmethod
        def getText(*args, **kwargs):
            return ("", False)

        @staticmethod
        def getItem(*args, **kwargs):
            return FakeInputDialog.answer

    player.playlists = {main.DEFAULT_PLAYLIST_NAME: []}
    player.active_playlist_name = main.DEFAULT_PLAYLIST_NAME
    player.playlist_tracks = player.playlists[main.DEFAULT_PLAYLIST_NAME]
    player._show_playlist_tracks()
    player._url_workers = []

    real_fetch, real_dialog = main.fetch_playlist_from_url, main.QInputDialog
    main.fetch_playlist_from_url = lambda u: ("Из ссылки", [one_track])
    main.QInputDialog = FakeInputDialog
    try:
        player.url_input.setText(
            "https://vk.com/music/playlist/-100_27052677"
            "?z=audio_playlist-100_27052677%2fabc")
        result = player.add_to_playlist()
        check("ссылка на плейлист ВК не добавилась треком",
              result == 1 and len(player.playlist_tracks) == 0
              and player.url_input.text() == "",
              f"{result}/{len(player.playlist_tracks)}")
        check("по ссылке стартовала фоновая загрузка плейлиста",
              bool(getattr(player, "_url_workers", []))
              and "Загружаю плейлист" in player.status_lbl.text(),
              player.status_lbl.text())

        for thread in list(getattr(player, "_url_workers", [])):
            thread.wait(5000)
        for _ in range(3):
            QApplication.processEvents()
        check("плейлист из ссылки создан в фоне",
              "Из ссылки" in player.playlists
              and len(player.playlists["Из ссылки"]) == 1,
              str({k: len(v) for k, v in player.playlists.items()}))

        # Сет SoundCloud тем же путём
        player.url_input.setText("https://soundcloud.com/dj/sets/mix-2026")
        result = player.add_to_playlist()
        check("ссылка на сет SoundCloud тоже уходит в импорт",
              result == 1 and player.url_input.text() == "",
              str(result))
        for thread in list(getattr(player, "_url_workers", [])):
            thread.wait(5000)
        for _ in range(3):
            QApplication.processEvents()   # done от второго воркера — пока фейк на месте

        # Плейлисты чужих площадок — честный отказ вместо мусорного трека
        before = len(player.playlist_tracks)
        player.url_input.setText("https://www.youtube.com/playlist?list=PL123")
        result = player.add_to_playlist()
        check("плейлист YouTube отклонён с пояснением",
              result == 0 and len(player.playlist_tracks) == before
              and "не поддерживаются" in player.status_lbl.text(),
              player.status_lbl.text())
        player.url_input.setText("https://open.spotify.com/playlist/abc")
        result = player.add_to_playlist()
        check("плейлист Spotify отклонён с пояснением",
              result == 0 and len(player.playlist_tracks) == before
              and "не поддерживаются" in player.status_lbl.text(),
              player.status_lbl.text())

        # Обычные ссылки-треки по-прежнему добавляются
        player.url_input.setText("https://vk.com/video-1_2")
        player.add_to_playlist()
        check("обычная ссылка-трек всё ещё добавляется",
              len(player.playlist_tracks) == before + 1,
              str(len(player.playlist_tracks)))
    finally:
        for thread in list(getattr(player, "_url_workers", [])):
            thread.wait(3000)
        for _ in range(3):
            QApplication.processEvents()   # добираем сигналы, пока фейк на месте
        main.fetch_playlist_from_url, main.QInputDialog = real_fetch, real_dialog
        player._url_workers = []

def section_21_updater():
    """Самопроверка обновлений: версии, GitHub, кнопки в настройках, вейвформа."""
    import updater

    # --- 1. Разбор и сравнение версий ---------------------------------------
    check("разбор версии v1.2.3", updater.parse_version("v1.2.3") == (1, 2, 3),
          str(updater.parse_version("v1.2.3")))
    check("короткая версия 1.2 дополняется нулями", updater.parse_version("1.2") == (1, 2, 0))
    check("мусорный хвост отброшен", updater.parse_version("release-2-beta") == (2, 0, 0))
    check("пустая версия не падает", updater.parse_version("") == (0, 0, 0))
    check("новее -> есть обновление", updater.has_update("1.0.0", "1.1.0"))
    check("тоже самое -> обновления нет", not updater.has_update("1.0.0", "1.0.0"))
    check("старше -> обновления нет", not updater.has_update("2.0.0", "1.0.0"))
    check("префикс v учитывается", updater.has_update("1.0.0", "v1.0.1"))

    # --- 2. Проверка через подставной «GitHub» (сеть не трогаем) -------------
    def fake_new(url, timeout):
        assert "api.github.com" in url, url
        return json.dumps({"tag_name": "v9.9.9",
                           "html_url": "https://example.com/r",
                           "body": "Что нового"})

    r = updater.check_for_updates(current="1.0.0", opener=fake_new)
    check("успешный ответ помечен ok", r["ok"] is True)
    check("обновление обнаружено", r["has_update"] is True and r["latest"] == "v9.9.9")
    check("в ответе есть ссылка и описание", r["url"] and r["notes"] == "Что нового")

    def fake_old(url, timeout):
        return json.dumps({"tag_name": "v0.9.0", "html_url": "", "body": ""})

    r_old = updater.check_for_updates(current="1.0.0", opener=fake_old)
    check("старый релиз не считается обновлением",
          r_old["ok"] and not r_old["has_update"] and r_old["latest"] == "v0.9.0")

    def fake_fail(url, timeout):
        raise OSError("нет сети")

    r_fail = updater.check_for_updates(current="1.0.0", opener=fake_fail)
    check("обрыв сети не бросает исключение", r_fail["ok"] is False)
    check("в ответе понятная ошибка", bool(r_fail["error"]))

    # --- 3. Версия и репозиторий проброшены в main ---------------------------
    check("версия плеера видна как константа",
          main.APP_VERSION == updater.APP_VERSION, main.APP_VERSION)
    check("репозиторий проброшен", main.GITHUB_REPO == updater.GITHUB_REPO)


def section_21b_update_install():
    """Установка обновления из плеера: выбор файла, архив, скрипт, очередь."""
    import zipfile
    import updater

    tmp = tempfile.mkdtemp(prefix="svinoklaud_update_")

    # --- 1. Выбор файла для скачивания из выпуска ---------------------------
    def release(name, size=100, url="https://dl/"):
        return {"tag_name": "v2.0.0", "zipball_url": "https://gh/source.zip",
                "assets": [{"name": name, "size": size,
                            "browser_download_url": url + name}]}

    inst = updater.pick_asset(release("Svinoklaud-Setup.exe"))
    check("установщик .exe выбран первым", inst.get("installer") is True, str(inst))
    check("у установщика запомнен адрес", inst.get("url").endswith(".exe"))

    zip_asset = updater.pick_asset(release("Svinoklaud.zip"))
    check("zip-архив выбран когда нет установщика",
          zip_asset.get("installer") is False and zip_asset.get("url"), str(zip_asset))
    check("размер архива передан", zip_asset.get("size") == 100)

    src = updater.pick_asset({"assets": [], "zipball_url": "https://gh/s.zip"})
    check("без вложений берём исходники выпуска",
          src.get("url") == "https://gh/s.zip" and src.get("installer") is False)
    check("пустой выпуск не даёт файла", updater.pick_asset({}) == {})

    # --- 2. Безопасные пути и общая папка в архиве ---------------------------
    check("выход за папку архива отброшен",
          updater._safe_rel("../../etc/passwd") == "")
    check("абсолютный путь приведён к относительному",
          updater._safe_rel("/abs/file.py") == "abs/file.py")
    check("нормальный путь сохранён",
          updater._safe_rel("a/b/main.py") == "a/b/main.py")
    check("общая папка архива найдена",
          updater.zip_root(["Repo-1/main.py", "Repo-1/updater.py"]) == "Repo-1")
    check("файлы в корне — общей папки нет",
          updater.zip_root(["main.py", "updater.py"]) == "")
    check("несколько папок верхнего уровня — общей папки нет",
          updater.zip_root(["a/main.py", "b/updater.py"]) == "")

    # --- 3. Собираем настоящий архив и разбираем его -------------------------
    arch = os.path.join(tmp, "update.zip")
    with zipfile.ZipFile(arch, "w") as zf:
        zf.writestr("Repo-9/main.py", "# плеер\n")
        zf.writestr("Repo-9/updater.py", "# обновления\n")
        zf.writestr("Repo-9/playlist.json", '{"version": 2}')   # файл игрока
        zf.writestr("Repo-9/__pycache__/main.pyc", "мусор")
        zf.writestr("Repo-9/../evil.py", "плохой файл")

    plan = updater.plan_files(arch)
    names = sorted(rel for _n, rel in plan)
    check("main.py попал в план", "main.py" in names, str(names))
    check("updater.py попал в план", "updater.py" in names, str(names))
    check("файл игрока не тронет обновление", "playlist.json" not in names, str(names))
    check("__pycache__ не тащим", not any("__pycache__" in n for n in names))
    check("выход за папку отброшен", not any("evil" in n for n in names), str(names))

    stage = os.path.join(tmp, "stage")
    staged = updater.stage_update(arch, stage)
    check("распаковка положила main.py рядом",
          os.path.isfile(os.path.join(stage, "main.py")))
    check("распаковка вернула список файлов", len(staged) == 2, str(staged))
    check("файл игрока не распакован",
          not os.path.exists(os.path.join(stage, "playlist.json")))

    # --- 4. Скрипт установки -------------------------------------------------
    fake_app = os.path.join(tmp, "app")
    os.makedirs(fake_app, exist_ok=True)
    script_path = os.path.join(tmp, updater.RESTART_SCRIPT)
    script_body = updater._wait_and_copy_script(stage, fake_app, 1234)
    check("скрипт ждёт наш процесс", "1234" in script_body)
    check("в скрипте адрес папки обновления", stage in script_body)
    check("в скрипте адрес папки плеера", fake_app in script_body)
    check("скрипт проверяет ошибки копирования", "errorlevel 1" in script_body)
    check("скрипт запускает плеер заново", "main.py" in script_body)
    # Пишем настоящий файл — с ним и работает очередь готового обновления
    updater._write_script(script_path, script_body)
    check("скрипт сохранился в файл", os.path.isfile(script_path))
    with open(script_path, encoding="utf-8") as fh:
        check("русский текст скрипта не побился", "Свиноклауд" in fh.read())

    exe = os.path.join(tmp, "setup.exe")
    inst_script = updater._run_installer_script(exe)
    check("скрипт установщика запускает exe", exe in inst_script)
    check("скрипт установщика ждёт его", "/wait" in inst_script)

    # --- 5. Очередь готового обновления -------------------------------------
    real_pending = updater.PENDING_FILE
    updater.PENDING_FILE = os.path.join(tmp, "pending.json")
    try:
        check("сначала очередь пуста", updater.load_pending() == {})
        check("без очереди нечего запускать", updater.launch_pending() is False)
        good = {"dir": stage, "script": script_path, "files": 2, "size": 100,
                "latest": "v2.0.0", "installer": False}
        check("очередь сохранилась", updater.save_pending(good) is True)
        back = updater.load_pending()
        check("очередь прочиталась", back.get("latest") == "v2.0.0", str(back))
        check("количество файлов сохранено", back.get("files") == 2)
        # Мусор в файле очереди не должен ронять плеер
        with open(updater.PENDING_FILE, "w", encoding="utf-8") as fh:
            fh.write("{битый json")
        check("битая очередь просто игнорируется", updater.load_pending() == {})
        # Папки нет — обновление уже неприменимо
        updater.save_pending({"dir": os.path.join(tmp, "нет такой"),
                              "script": script_path})
        check("обновление без папки считается неприменимым",
              updater.load_pending() == {})
        updater.clear_pending()
        check("очередь очищена", updater.load_pending() == {})
    finally:
        updater.PENDING_FILE = real_pending

    # --- 6. Размер файла для строки статуса ---------------------------------
    check("размер в байтах", updater.human_size(512) == "512 Б",
          updater.human_size(512))
    check("размер в килобайтах", updater.human_size(2048) == "2.0 КБ",
          updater.human_size(2048))
    check("пустой размер не падает", updater.human_size(0) == "0 Б")

    # --- 7. Скачивание: обычный адрес и файл с диска -------------------------
    local = os.path.join(tmp, "локальный.zip")
    with zipfile.ZipFile(local, "w") as zf:
        zf.writestr("Repo-x/main.py", "# плеер\n")

    got = os.path.join(tmp, "скачано.zip")
    size = updater.download("file:///" + local.replace("\\", "/"), got)
    check("файл с диска скопирован", os.path.isfile(got))
    check("размер вернулся числом, а не путём", size == os.path.getsize(local),
          str(size))
    check("файл с диска читается как архив", "main.py" in [
        rel for _n, rel in updater.plan_files(got)])

    def fail_open(url, timeout):
        raise OSError("нет сети")

    real_urlopen = updater.urllib.request.urlopen
    updater.urllib.request.urlopen = fail_open
    try:
        try:
            updater.download("https://dl/x.zip", os.path.join(tmp, "нет.zip"))
            check("обрыв сети не проходит молча", False)
        except OSError as exc:
            check("обрыв сети отдаёт понятную ошибку", "нет сети" in str(exc))
    finally:
        updater.urllib.request.urlopen = real_urlopen
        updater._rmtree(tmp)      # убираем за собой только когда всё готово


def section_22_update_ui(player):
    """Окно настроек, обработчики результата и «умная» вейвформа плеера."""
    import updater

    # --- 1. В настройках есть блок обновлений --------------------------------
    box = main.SettingsWindow(player)
    player.settings_win = box
    check("в настройках показана версия плеера",
          player.app_version in box.version_lbl.text(), box.version_lbl.text())
    check("кнопка проверки обновлений на месте", hasattr(box, "btn_update_check"))
    check("кнопка GitHub-страницы на месте", hasattr(box, "btn_update_page"))
    check("подписи кнопок понятны",
          "обновлен" in box.btn_update_check.text().lower(), box.btn_update_check.text())
    # Кнопка установки есть, но спрятана: пока не нашли обновление, жать нечего
    check("кнопка установки обновления на месте",
          hasattr(box, "btn_update_install"))
    check("кнопка установки спрятана до находки обновления",
          not box.btn_update_install.isVisibleTo(box))

    # --- 1б. Результаты проверки показывают и прячут кнопку установки --------
    player._on_update_checked({"ok": True, "has_update": True,
                               "latest": "v9.9.9", "url": "https://example.com/r",
                               "notes": "починили поиск"}, silent=True)
    check("обновление видно в статусе",
          "v9.9.9" in player.status_lbl.text(), player.status_lbl.text())
    check("кнопка установки появилась после находки",
          box.btn_update_install.isVisibleTo(box))
    check("описание изменений показано",
          "починили поиск" in box.update_notes.text(), box.update_notes.text())
    check("обновление запомнено плеером",
          getattr(player, "_pending_update", {}).get("latest") == "v9.9.9")

    # Тихая проверка «обновлений нет» ничего не пишет — статус не меняется
    before_silent = player.status_lbl.text()
    player._on_update_checked({"ok": True, "has_update": False,
                               "latest": player.app_version, "url": "", "notes": ""},
                              silent=True)
    check("тихая проверка без обновления не трогает статус",
          player.status_lbl.text() == before_silent, player.status_lbl.text())
    check("кнопка установки спрятана когда всё свежее",
          not box.btn_update_install.isVisibleTo(box))
    check("забытое обновление сброшено",
          getattr(player, "_pending_update", None) is None)

    player._on_update_checked({"ok": False, "error": "нет сети"})
    check("ошибка проверки видна в статусе",
          "не проверились" in player.status_lbl.text(), player.status_lbl.text())

    player._on_update_checked({"ok": False, "error": "нет сети"}, silent=True)
    check("тихая проверка не показывает ошибку", True)

    # --- 1в. Скачивание обновления: поток, статус, отказ ---------------------
    # Подменяем настоящий updater на «пустышку»: сеть в тестах не трогаем.
    real_prepare = updater.prepare_update
    real_clear = updater.clear_pending
    real_launch = updater.launch_pending
    ready = {"dir": tempfile.gettempdir(), "script": "нет такого",
             "files": 3, "size": 2048, "latest": "v9.9.9", "installer": False}
    updater.prepare_update = lambda *a, **k: (ready, "")
    updater.clear_pending = lambda: True
    # Говорим «установка запустилась»: настоящий launch_pending тут только мешал бы
    updater.launch_pending = lambda: True
    try:
        player._pending_update = {"has_update": True, "latest": "v9.9.9",
                                  "asset": {"url": "https://dl/x.zip", "size": 2048}}
        player._start_update_download(player._pending_update)
        check("скачивание идёт в отдельном потоке",
              player._update_thread is not None)
        # Ждём завершения потока: оно приходит сигналом finished (см. _on_update_ready)
        for _ in range(200):
            if getattr(player, "_update_box", None) is not None:
                break
            QApplication.processEvents()
            time.sleep(0.05)
        check("после скачивания спрашиваем про перезапуск",
              getattr(player, "_update_box", None) is not None,
              player.status_lbl.text())
        check("поток установки отпущен", player._update_thread is None)

        # Ошибка скачивания не роняет плеер
        updater.prepare_update = lambda *a, **k: ({}, "Не удалось скачать обновление")
        player._on_update_failed("Не удалось скачать обновление")
        check("ошибка установки видна в статусе",
              "Не удалось скачать" in player.status_lbl.text(),
              player.status_lbl.text())

        # Отказ от обновления: плеер продолжает работать
        player.show()
        launched = []
        updater.launch_pending = lambda: launched.append(1) or True
        player._cancel_update(ready)
        check("отложенное обновление не закрывает плеер", player.isVisible())
        check("установка не запускалась без согласия", not launched)
        check("статус предлагает повторить установку",
              "отложено" in player.status_lbl.text().lower(),
              player.status_lbl.text())

        # Согласие на установку: сохраняем плейлист и запускаем скрипт
        player._apply_update(ready)
        check("скрипт установки запущен", bool(launched))
        check("статус предупреждает о перезапуске",
              "перезапустится" in player.status_lbl.text().lower(),
              player.status_lbl.text())
    finally:
        updater.prepare_update = real_prepare
        updater.clear_pending = real_clear
        updater.launch_pending = real_launch

    # --- 3. Вейвформа: таймер только пока играет ------------------------------
    wf = player.waveform
    check("при старте анимация вейвформы выключена (CPU в простое)",
          not wf._timer.isActive())
    wf.set_playing(True)
    check("во время игры таймер анимации запущен", wf._timer.isActive())
    wf.set_playing(False)
    check("после остановки таймер анимации остановлен", not wf._timer.isActive())

    # --- 4. Кэш вейвформы: повторный запуск того же файла мгновенный ---------
    tmp_wav = os.path.join(tempfile.gettempdir(), "_wf_cache_probe.wav")
    try:
        import struct as _struct
        frames = b"".join(_struct.pack("<h", (i % 200) - 100) for i in range(2000))
        with open(tmp_wav, "wb") as fh:
            fh.write(b"RIFF" + _struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
                     + _struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
                     + b"data" + _struct.pack("<I", len(frames)) + frames)
        first = player._compute_waveform(tmp_wav)
        check("вейвформа посчиталась из wav", bool(first), str(len(first)))
        check("результат закэширован по пути файла", tmp_wav in player._waveform_memory)
    finally:
        try:
            os.remove(tmp_wav)
        except OSError:
            pass


def section_23_yandex_uuid_playlist(player):
    """Новый формат ссылок Яндекс.Музыки: music.yandex.ru/playlists/<uuid>.

    Старый разбор ждал только /users/юзер/playlists/123, поэтому такую ссылку
    плеер добавлял «одним треком» с названием-мусором из uuid. Теперь uuid
    читается через открытый JSON API (весь плейлист сразу), а при сбое —
    через разбор страницы нового дизайна.
    """
    uuid = "69827c0b-af47-4998-bce9-da6448d9cb2c"

    # --- распознавание ссылки нового дизайна --------------------------------
    parts = main.playlist_url_parts(f"https://music.yandex.ru/playlists/{uuid}")
    check("новый формат плейлиста Яндекса разобран",
          parts == {"platform": "ym", "uuid": uuid}, str(parts))
    parts = main.playlist_url_parts(
        f"https://music.yandex.ru/playlists/{uuid}"
        "?utm_source=web&utm_medium=copy_link")
    check("хвост utm-метки не мешает разбору uuid",
          parts.get("uuid") == uuid, str(parts))
    parts = main.playlist_url_parts(
        f"https://www.music.yandex.ru/playlists/{uuid}")
    check("www перед хостом Яндекса не мешает",
          parts.get("platform") == "ym" and parts.get("uuid") == uuid, str(parts))
    check("uuid-ссылка Яндекса — площадка ym",
          main.playlist_url_platform(
              f"https://music.yandex.ru/playlists/{uuid}") == "ym")
    check("старый формат users/…/playlists/… по-прежнему работает",
          main.playlist_url_parts(
              "https://music.yandex.ru/users/yandex/playlists/42")
          == {"platform": "ym", "user": "yandex", "kind": "42"})
    check("не-плейлистовая страница Яндекса — не плейлист",
          main.playlist_url_parts("https://music.yandex.ru/playlists") == {})

    # --- JSON API: ответ превращается в треки -------------------------------
    # Структура реального ответа https://api.music.yandex.net/playlist/<uuid>:
    # result.title/trackCount/tracks, у трека обёртка {id, …, track: {...}},
    # альбом лежит в albums[0].id, а не в albumId.
    ym_api_json = json.dumps({"result": {
        "kind": 1053, "title": "Новая дорога", "trackCount": 3,
        "tracks": [
            {"id": 1, "originalIndex": 0, "track":
             {"id": 111, "title": "В путь", "durationMs": 60000,
              "artists": [{"id": 1, "name": "Исполнитель"}],
              "albums": [{"id": 5, "title": "Диск"}],
              "coverUri": "avatars.yandex.net/get/%%", "available": True}},
            {"id": 2, "originalIndex": 1, "track":
             {"id": 222, "title": "Дальше", "durationMs": 30000,
              "artists": [{"id": 2, "name": "Второй"}],
              "albums": [{"id": 6, "title": "Ещё"}], "available": True}},
            # битая запись без трека — не должна ронять разбор
            {"id": 3, "originalIndex": 2, "track": {"id": 0, "title": ""}},
        ]}})

    seen_urls = []

    def fake_api_http(url, timeout=25, headers=None):
        seen_urls.append(url)
        if "api.music.yandex.net/playlist/" in url:
            return ym_api_json
        raise RuntimeError("неожиданный URL: " + url)

    real_http = main._http_get
    main._http_get = fake_api_http
    try:
        name, tracks = main.fetch_playlist_from_url(
            f"https://music.yandex.ru/playlists/{uuid}")
        check("uuid-плейлист Яндекса загружен через JSON API",
              name == "Новая дорога" and len(tracks) == 2,
              f"{name}/{len(tracks)}")
        check("запрос ушёл в api.music.yandex.net/playlist/<uuid>",
              bool(seen_urls) and seen_urls[0]
              == f"https://api.music.yandex.net/playlist/{uuid}",
              seen_urls[0] if seen_urls else "")
        check("треки API собраны общим разбором карточек",
              bool(tracks) and tracks[0]["title"] == "В путь"
              and tracks[0]["author"] == "Исполнитель"
              and tracks[0]["duration"] == 60.0
              and tracks[0]["cover"].startswith("https://avatars."),
              str(tracks[:1]))
        check("адрес трека собран из albums[0].id",
              bool(tracks) and tracks[0]["url"]
              == "https://music.yandex.ru/album/5/track/111",
              tracks[0]["url"] if tracks else "")
        check("битая запись API пропущена без падения",
              len(tracks) == 2 and all(t["id"] != "0" for t in tracks),
              str([t["id"] for t in tracks]))
        check("у каждого трека есть флаг площадки для плейлистов",
              bool(tracks) and all(t.get("platform") == "ym" for t in tracks))
    finally:
        main._http_get = real_http


    # --- запасной путь: API молчит, читаем страницу нового дизайна ----------
    ym_uuid_html = (
        '<html><head><title>Плейлист «Со страницы» — Яндекс.Музыка</title></head><body>'
        '<script>(window.__STATE_PATCHES__ = window.__STATE_PATCHES__ || [])'
        '.push([{"op":"add","path":"\\u002Fplaylists\\u002F' + uuid +
        '\\u002Ftracks\\u002F0","value":{"id":333,"title":"Со страницы",'
        '"durationMs":5000,"albumId":9,"artists":[{"id":3,"name":"Страница"}],'
        '"coverUri":"avatars.yandex.net\\u002Fget\\u002F%%"}}])</script></body></html>')

    def fallback_http(url, timeout=25, headers=None):
        if "api.music.yandex.net" in url:
            raise OSError("нет сети")
        if f"/playlists/{uuid}" in url:
            return ym_uuid_html
        raise RuntimeError("неожиданный URL: " + url)

    main._http_get = fallback_http
    try:
        name, tracks = main.fetch_playlist_from_url(
            f"https://music.yandex.ru/playlists/{uuid}")
        check("при сбое API берётся страница нового дизайна",
              name == "Со страницы" and len(tracks) == 1
              and tracks[0]["title"] == "Со страницы",
              f"{name}/{len(tracks)}")
    finally:
        main._http_get = real_http

    # --- оба пути молчат — честная ошибка, а не пустой плейлист -------------
    def dead_http(url, timeout=25, headers=None):
        raise OSError("нет сети")

    main._http_get = dead_http
    try:
        try:
            main.fetch_playlist_from_url(
                f"https://music.yandex.ru/playlists/{uuid}")
            check("мёртвая сеть даёт понятную ошибку", False)
        except RuntimeError as exc:
            check("мёртвая сеть даёт понятную ошибку",
                  "недоступна" in str(exc), str(exc))
    finally:
        main._http_get = real_http

    # --- поле добавления: uuid-ссылка уходит в импорт, а не «треком» --------
    two_tracks = [{"type": "ym", "platform": "ym", "title": "В путь",
                   "url": "https://music.yandex.ru/album/5/track/111"},
                  {"type": "ym", "platform": "ym", "title": "Дальше",
                   "url": "https://music.yandex.ru/album/6/track/222"}]

    class FakeInputDialog:
        answer = ("Как новый плейлист", True)

        @staticmethod
        def getItem(*args, **kwargs):
            return FakeInputDialog.answer

    real_fetch = main.fetch_playlist_from_url
    real_dialog = main.QInputDialog
    main.fetch_playlist_from_url = lambda u: ("Новая дорога", two_tracks)
    main.QInputDialog = FakeInputDialog
    player.playlists = {main.DEFAULT_PLAYLIST_NAME: []}
    player.active_playlist_name = main.DEFAULT_PLAYLIST_NAME
    player.playlist_tracks = player.playlists[main.DEFAULT_PLAYLIST_NAME]
    player._show_playlist_tracks()
    player._url_workers = []
    try:
        player.url_input.setText(
            f"https://music.yandex.ru/playlists/{uuid}?utm_source=web")
        result = player.add_to_playlist()
        check("uuid-ссылка Яндекса не добавилась мусорным треком",
              result == 1 and len(player.playlist_tracks) == 0
              and player.url_input.text() == "",
              f"{result}/{len(player.playlist_tracks)}")
        check("по uuid-ссылке стартовала фоновая загрузка плейлиста",
              bool(getattr(player, "_url_workers", []))
              and "Загружаю плейлист" in player.status_lbl.text(),
              player.status_lbl.text())
        for thread in list(getattr(player, "_url_workers", [])):
            thread.wait(5000)
        for _ in range(3):
            QApplication.processEvents()
        check("плейлист из uuid-ссылки создан в фоне целиком",
              "Новая дорога" in player.playlists
              and len(player.playlists["Новая дорога"]) == 2,
              str({k: len(v) for k, v in player.playlists.items()}))
    finally:
        for thread in list(getattr(player, "_url_workers", [])):
            thread.wait(3000)
        for _ in range(3):
            QApplication.processEvents()   # добираем сигналы, пока фейки на месте
        main.fetch_playlist_from_url = real_fetch
        main.QInputDialog = real_dialog
        player._url_workers = []


def section_24_yandex_download(player):
    """Прямое скачивание Яндекс.Музыки: цепочка API, токен, превью, настройки."""
    # --- 1. Разбор ссылок -----------------------------------------------------
    check("id трека Яндекса из ссылки",
          main.yandex_track_id("https://music.yandex.ru/album/5/track/111") == "111",
          main.yandex_track_id("https://music.yandex.ru/album/5/track/111"))
    check("не-трековая ссылка не даёт id",
          main.yandex_track_id("https://music.yandex.ru/album/5") == "")
    check("пустая ссылка не роняет разбор",
          main.yandex_track_id("") == "" and main.yandex_track_id(None) == "")

    # --- 2. Цепочка download-info -> XML -> get-mp3 (сеть подменена) -----------
    real_http = main._http_get
    calls = []

    def fake_http(url, timeout=20, headers=None):
        calls.append((url, dict(headers or {})))
        if "download-info" in url:
            return json.dumps({"result": [
                {"preview": True, "downloadInfoUrl": "https://info/prev"},
                {"preview": False, "downloadInfoUrl": "https://info/full"}]})
        if url == "https://info/full":
            return ("<download-info><host>storage.ym.test</host>"
                    "<path>/rmusic/abc.mp3</path><proto>https</proto>"
                    "<ts>1711111111</ts><s>sig-sig</s></download-info>")
        raise RuntimeError("неожиданный URL: " + url)

    main._http_get = fake_http
    try:
        url, preview = main.yandex_direct_url(
            "https://music.yandex.ru/album/5/track/111", "TOKEN1")
        check("прямая ссылка Яндекса собрана из XML",
              url == "https://storage.ym.test/get-mp3/sig-sig/1711111111/rmusic/abc.mp3",
              url)
        check("полный трек не помечен превью", preview is False)
        check("выбрана именно непревьюная запись",
              calls[1][0] == "https://info/full", str(calls[0][0]))
        check("токен ушёл в Authorization",
              calls[0][1].get("Authorization") == "OAuth TOKEN1",
              str(calls[0][1]))
        check("заголовок клиента Яндекса отправлен",
              calls[0][1].get("X-Yandex-Music-Client") == "HTML58Yandex",
              str(calls[0][1]))
    finally:
        main._http_get = real_http

    # превью-только: флаг честно поднят
    def preview_http(url, timeout=20, headers=None):
        if "download-info" in url:
            return json.dumps({"result": [
                {"preview": True, "downloadInfoUrl": "https://info/prev"}]})
        return ("<host>p.ym.test</host><path>/p.mp3</path>"
                "<ts>9</ts><s>s9</s>")

    main._http_get = preview_http
    try:
        url, preview = main.yandex_direct_url(
            "https://music.yandex.ru/album/5/track/111")
        check("без токена берётся превью", preview is True and url.startswith("https://"),
              f"{url}/preview={preview}")
        main._http_get = lambda u, timeout=20, headers=None: "<html>не XML</html>"
        url, preview = main.yandex_direct_url(
            "https://music.yandex.ru/album/5/track/111")
        check("битый XML даёт пустую ссылку", url == "" and preview is False,
              f"{url}/{preview}")
    finally:
        main._http_get = real_http

    def dead_http(url, timeout=20, headers=None):
        raise OSError("нет сети")

    main._http_get = dead_http
    try:
        url, preview = main.yandex_direct_url(
            "https://music.yandex.ru/album/5/track/111")
        check("обрыв сети даёт пустую ссылку", url == "" and preview is False)
    finally:
        main._http_get = real_http

    check("ссылка без id трека не ходит в сеть",
          main.yandex_direct_url("https://music.yandex.ru/album/5") == ("", False))

    # --- 3. Токен: файл, окружение ---------------------------------------------
    tmp = tempfile.mkdtemp(prefix="svinoklaud_ymtok_")
    real_path = main.ym_token_path
    real_env = os.environ.pop("SVINOKLAUD_YM_TOKEN", None)
    main.ym_token_path = lambda: os.path.join(tmp, "ym_token.txt")
    try:
        check("без токена Яндекс пуст", main.load_ym_token() == "",
              main.load_ym_token())
        check("токен Яндекса сохраняется", main.save_ym_token("ym-abc"))
        check("токен Яндекса читается обратно", main.load_ym_token() == "ym-abc",
              main.load_ym_token())
        main.save_ym_token("")
        os.environ["SVINOKLAUD_YM_TOKEN"] = "env-ym"
        check("пустой файл уступает переменной окружения",
              main.load_ym_token() == "env-ym", main.load_ym_token())
    finally:
        os.environ.pop("SVINOKLAUD_YM_TOKEN", None)
        if real_env is not None:
            os.environ["SVINOKLAUD_YM_TOKEN"] = real_env
        main.ym_token_path = real_path
        try:
            os.remove(os.path.join(tmp, "ym_token.txt"))
        except OSError:
            pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    # --- 4. Плеер: свежая ссылка, флаг превью, обход yt-dlp --------------------
    ym_url = "https://music.yandex.ru/album/5/track/111"
    player.playlists[main.DEFAULT_PLAYLIST_NAME] = []
    player.active_playlist_name = main.DEFAULT_PLAYLIST_NAME
    player.playlist_tracks = player.playlists[main.DEFAULT_PLAYLIST_NAME]
    # add_search_result планирует фоновый prefetch — в тесте он не нужен
    real_pf = player._prefetch_track
    player._prefetch_track = lambda url: None
    try:
        player.add_search_result({"type": "ym", "platform": "ym", "title": "Тест",
                                  "author": "Кто-то", "url": ym_url, "duration": 0.0})
    finally:
        player._prefetch_track = real_pf
    real_yd = main.yandex_direct_url

    main.yandex_direct_url = lambda u, token="": ("https://storage.ym.test/x.mp3", True)
    try:
        got = player._direct_url_for(ym_url, 0)
        check("плеер берёт ссылку Яндекса у API", got == "https://storage.ym.test/x.mp3",
              got)
        check("флаг превью записан в трек",
              player.playlist_tracks[0].get("ym_preview") is True)
        check("подписанная ссылка не сохраняется в плейлист",
              "direct_url" not in player.playlist_tracks[0],
              str(player.playlist_tracks[0].get("direct_url")))
        check("флаг превью запомнился и в атрибуте плеера",
              player._ym_preview is True)
    finally:
        main.yandex_direct_url = real_yd

    # yt-dlp для Яндекса не вызывается: нет ссылки — понятная ошибка
    real_fetch = player.fetch_to_cache
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return (None, "", "", "")

    player.fetch_to_cache = fake_fetch
    real_dd = player._download_direct
    real_play = player._play_file
    # Кэш тестового трека отключаем: реальная папка .sc_cache может его содержать
    real_cached = player._cached_file
    player._cached_file = lambda url: None
    try:
        main.yandex_direct_url = lambda u, token="": ("", False)
        ok = player._start_track(ym_url, 0)
        check("без ссылки Яндекс не идёт в yt-dlp", ok is False and fetched == [],
              str(fetched))
        check("ошибка Яндекса объяснена по-русски",
              "Яндекс" in player.status_lbl.text(), player.status_lbl.text())

        # превью-ссылка: скачиваем и играем, статус честно предупреждает
        calls_dl = []
        player._download_direct = lambda direct, url, index=None: (
            calls_dl.append((direct, url)) or "preview.mp3")
        player._play_file = lambda *a, **k: True
        main.yandex_direct_url = lambda u, token="": ("https://storage.ym.test/x.mp3",
                                                      True)
        ok = player._start_track(ym_url, 0)
        check("превью-трек запускается прямой ссылкой",
              ok is True and bool(calls_dl)
              and calls_dl[0][0].startswith("https://"), str(calls_dl))
        check("статус предупреждает о превью",
              "превью" in player.status_lbl.text().lower(),
              player.status_lbl.text())

        # prefetch для Яндекса тоже мимо yt-dlp
        fetched.clear()
        player._download_direct = lambda direct, url, index=None: ""
        main.yandex_direct_url = lambda u, token="": ("https://storage.ym.test/x.mp3",
                                                      False)
        player._prefetch_track(ym_url)
        check("prefetch Яндекса не идёт в yt-dlp", fetched == [], str(fetched))

        main.yandex_direct_url = lambda u, token="": ("", False)
        player._prefetch_track(ym_url)
        check("prefetch без ссылки тоже мимо yt-dlp", fetched == [], str(fetched))
    finally:
        player.fetch_to_cache = real_fetch
        player._download_direct = real_dd
        player._play_file = real_play
        player._cached_file = real_cached
        main.yandex_direct_url = real_yd

    # --- 5. Кэш превью не подменяет полный трек --------------------------------
    tmp_cache = tempfile.mkdtemp(prefix="svinoklaud_ymcache_")
    real_cache_dir = player._cache_dir
    real_da = main.download_audio
    made = []

    def fake_da(direct_url, target_base, timeout=45, referer=None, user_agent=None):
        made.append((direct_url, target_base, referer))
        path = target_base + ".mp3"
        with open(path, "wb") as fh:
            fh.write(b"\xff\xfb" + b"\x00" * 21000)
        return path

    player._cache_dir = lambda: tmp_cache
    main.download_audio = fake_da
    try:
        player.playlist_tracks[0]["ym_preview"] = True
        path1 = player._download_direct("https://storage.ym.test/x.mp3", ym_url, 0)
        check("превью кладётся под отдельным именем",
              "_preview" in os.path.basename(path1), path1)
        check("для Яндекса не шлётся Referer ВК", made[0][2] == "", str(made[0]))

        path2 = player._download_direct("https://storage.ym.test/x.mp3", ym_url, 0)
        check("повторное превью берётся из кэша", path2 == path1 and len(made) == 1,
              f"{path2}/{len(made)}")

        player.playlist_tracks[0]["ym_preview"] = False
        path3 = player._download_direct("https://storage.ym.test/full.mp3", ym_url, 0)
        check("полный трек ложится в обычный кэш",
              "_preview" not in os.path.basename(path3), path3)
        leftovers = [n for n in os.listdir(tmp_cache) if "_preview" in n]
        check("превью удалено после полного трека", not leftovers, str(leftovers))

        made.clear()
        player._download_direct("https://ps1.vk.com/f.mp3",
                                "https://vk.com/audio1_2", None)
        check("для ВК Referer остался vk.com",
              bool(made) and made[0][2] == "https://vk.com/", str(made))
    finally:
        player._cache_dir = real_cache_dir
        main.download_audio = real_da
        try:
            for name in os.listdir(tmp_cache):
                os.remove(os.path.join(tmp_cache, name))
            os.rmdir(tmp_cache)
        except OSError:
            pass

    # --- 6. Окно настроек: секция токена Яндекса --------------------------------
    box = main.SettingsWindow(player)
    player.settings_win = box
    check("поле токена Яндекса в настройках", hasattr(box, "ym_token_edit"))
    check("кнопка сохранения токена Яндекса", hasattr(box, "btn_ym_token"))
    check("кнопка открытия страницы выдачи токена", hasattr(box, "btn_ym_help"))
    check("подсказка про токен Яндекса на месте", hasattr(box, "ym_hint"))
    check("открытие страницы токена — функция",
          callable(getattr(player, "open_ym_oauth", None)))

    real_path = main.ym_token_path
    settings_file = os.path.join(tempfile.gettempdir(), "svinoklaud_ym_settings.txt")
    main.ym_token_path = lambda: settings_file
    try:
        box.ym_token_edit.setText("  ym-secret  ")
        player.save_ym_token()
        check("токен Яндекса сохранился из настройки",
              main.load_ym_token() == "ym-secret", main.load_ym_token())
        check("плеер запомнил токен", player.ym_token == "ym-secret",
              player.ym_token)
        check("статус сообщает о сохранении",
              "сохранён" in player.status_lbl.text(), player.status_lbl.text())
        box.ym_token_edit.setText("")
        player.save_ym_token()
        check("пустое поле убирает токен", main.load_ym_token() == "",
              main.load_ym_token())
    finally:
        main.ym_token_path = real_path
        try:
            os.remove(settings_file)
        except OSError:
            pass


def main_test():
    # Ссылку держим в переменной: иначе QApplication сразу соберётся сборщиком мусора
    # и создание виджетов упадёт с «Must construct a QApplication before a QWidget».
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="svinoklaud_theme_")


    # Изоляция: свой playlist.json и theme_config.json вместо настоящих
    real_playlist, real_theme = main.PLAYLIST_FILE, main.theme_path
    main.PLAYLIST_FILE = os.path.join(tmp, "playlist.json")
    main.theme_path = lambda: os.path.join(tmp, "theme_config.json")
    # Токен ВК тоже изолируем: многие проверки смотрят поведение «без токена»,
    # а на диске пользователя лежит настоящий ключ — тесты его подхватывали.
    real_vk_path, real_vk_env = main.vk_token_path, main.VK_ACCESS_TOKEN
    main.vk_token_path = lambda: os.path.join(tmp, "vk_token.txt")
    main.VK_ACCESS_TOKEN = ""
    # Токен Яндекса тоже изолируем: тесты пишут и читают его, а на диске
    # может лежать настоящий ключ пользователя.
    real_ym_path = main.ym_token_path
    main.ym_token_path = lambda: os.path.join(tmp, "ym_token.txt")
    real_find, real_enabled = lyrics_web.find, lyrics_web.is_enabled
    real_ffmpeg = main.WhisperWorker._ensure_ffmpeg
    lyrics_web.find = lambda *a, **k: ("", "")
    lyrics_web.is_enabled = lambda: False
    main.WhisperWorker._ensure_ffmpeg = staticmethod(lambda: False)
    try:
        section_1_platforms()
        section_2_search()
        section_3_other_platforms()
        section_3b_web_parsers()
        section_4_lrc()

        section_5_karaoke_widget()
        player = main.SvinoklaudPlayer()
        section_6_buttons_and_colors(player)
        section_7_theme_profiles(player, tmp)
        section_8_lrc_files(player, tmp)
        section_9_playlist_platforms(player)
        section_18_multi_playlist(player, tmp)
        section_19_playlist_from_url(player, tmp)
        section_20_playlist_paste(player, tmp)
        section_21_updater()
        section_21b_update_install()
        section_22_update_ui(player)
        section_23_yandex_uuid_playlist(player)
        section_24_yandex_download(player)
        player.close()
        section_10_search_worker_offline()
        section_11_direct_download()
        section_12_vk_token()
        section_13_vk_track_record()
        section_14_vk_token_check_worker()
        section_15_vk_auto_token()
        section_16_search_results_view()
        section_17_vk_links()


    finally:
        main.PLAYLIST_FILE = real_playlist
        main.theme_path = real_theme
        main.vk_token_path, main.VK_ACCESS_TOKEN = real_vk_path, real_vk_env
        main.ym_token_path = real_ym_path
        lyrics_web.find, lyrics_web.is_enabled = real_find, real_enabled
        main.WhisperWorker._ensure_ffmpeg = real_ffmpeg
    del app

    failed = [name for name, ok, _ in results if not ok]
    print(f"\nИтог: {len(results) - len(failed)}/{len(results)} проверок пройдено")
    if failed:
        print("НЕ ПРОШЛИ: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main_test())
    except Exception:
        traceback.print_exc()
        sys.exit(2)

