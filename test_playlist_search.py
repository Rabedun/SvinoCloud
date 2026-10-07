"""Проверка новых функций: поиск SoundCloud, память плейлиста, удаление треков.

Запуск:  python test_playlist_search.py     (код возврата 0 — все проверки прошли)

Тест работает офлайн и НЕ трогает ваш настоящий playlist.json: путь файла
подменяется на временный, а сеть и Whisper отключаются заглушками.
"""
import json
import os
import sys
import tempfile
import traceback
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402
from PyQt6.QtCore import QEventLoop, QTimer        # noqa: E402

import main          # noqa: E402
import lyrics_web    # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (f"  -> {detail}" if detail else ""))


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def make_wav(path, seconds=0.3):
    """Маленький настоящий WAV, чтобы pygame мог его загрузить."""
    rate = 22050
    frames = int(rate * seconds)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * frames)
    return path


# Реальный вывод yt-dlp для scsearch3 (проверен на живом поиске)
REAL_YTDLP_OUTPUT = (
    "Scatz X Kleanup Man - FLINT.MI\tPeur De Temps 3\t"
    "https://soundcloud.com/peur-de-temps-3/scatz-x-kleanup-man-flintmi\t117.4\n"
    "kleanup man-right on it\tclean up man\t"
    "https://soundcloud.com/user-62884185/kleanup-man-right-on-it\t121.462\n"
    "kleanup\tephrraiim\thttps://soundcloud.com/e-batista/kleanup-1\t74.443\n"
)


def main_test():
    app = QApplication(sys.argv)
    tmp = tempfile.mkdtemp(prefix="sc_playlist_")
    local_wav = make_wav(os.path.join(tmp, "my track.wav"))

    # --- Изоляция: свой playlist.json вместо настоящего --------------------
    real_playlist_file = main.PLAYLIST_FILE
    main.PLAYLIST_FILE = os.path.join(tmp, "playlist.json")
    real_find, real_enabled = lyrics_web.find, lyrics_web.is_enabled
    real_ffmpeg = main.WhisperWorker._ensure_ffmpeg
    lyrics_web.find = lambda *a, **k: ("", "")
    lyrics_web.is_enabled = lambda: False
    main.WhisperWorker._ensure_ffmpeg = staticmethod(lambda: False)

    try:
        return run_checks(app, tmp, local_wav)
    finally:
        main.PLAYLIST_FILE = real_playlist_file
        lyrics_web.find, lyrics_web.is_enabled = real_find, real_enabled
        main.WhisperWorker._ensure_ffmpeg = real_ffmpeg

class _StubPlayer(QWidget):
    """Минимальный родитель для окна поиска: нужны стили и добавление в плейлист."""

    def __init__(self):
        super().__init__()
        self.playlist_tracks = []
        self.played = ""

    def btn_style(self):
        return ""

    def add_search_result(self, track):
        self.playlist_tracks.append(track)
        return True

    def play_track_by_url(self, url):
        self.played = url


def run_checks(app, tmp, local_wav):
    stub_player = _StubPlayer()
    # --- 1. Команда поиска: префикс scsearch, а не ytsearch ------------------
    cmd = main.SearchWorker("KLEANUP").command()
    check("поиск идёт через yt-dlp (subprocess)", "yt_dlp" in cmd,
          " ".join(str(c) for c in cmd))
    check("используется scsearch — это поиск по SoundCloud",
          any("scsearch3:" in str(part) for part in cmd),
          " ".join(str(c) for c in cmd if "scsearch" in str(c)))
    check("нет лишней загрузки (--flat-playlist)", "--flat-playlist" in cmd)
    check("запрашиваются название, автор и ссылка",
          any("%(webpage_url)s" in str(c) for c in cmd))
    check("число результатов можно задать",
          "scsearch7:" in " ".join(str(c) for c in main.SearchWorker("x", 7).command()))

    # --- 2. Разбор ответа yt-dlp -------------------------------------------
    tracks = main.SearchWorker.parse(REAL_YTDLP_OUTPUT)
    check("найдены все 3 трека", len(tracks) == 3, str(len(tracks)))
    check("ссылка SoundCloud разобрана",
          bool(tracks) and tracks[0]["url"].startswith("https://soundcloud.com/"),
          tracks[0]["url"] if tracks else "")
    check("автор разобран", bool(tracks) and tracks[0]["author"] == "Peur De Temps 3")
    check("название разобрано", bool(tracks) and "Kleanup" in tracks[0]["title"])
    check("длительность — число",
          bool(tracks) and isinstance(tracks[0]["duration"], float) and tracks[0]["duration"] > 100,
          str(tracks[0]["duration"]) if tracks else "")
    check("мусорные строки игнорируются", main.SearchWorker.parse("мусор\nещё мусор") == [])
    check("пустой ответ без падения",
          main.SearchWorker.parse("") == [] and main.SearchWorker.parse(None) == [])

    # --- 2b. Русские буквы не превращаются в «кракозябры» ---------------------
    # Именно так выглядел сбой: вывод yt-dlp в cp1251 читался как UTF-8.
    cyrillic_output = REAL_YTDLP_OUTPUT.replace("Scatz X Kleanup Man - FLINT.MI",
                                              "Scatz Кривая Кость")
    cp1251_bytes = cyrillic_output.encode("cp1251")
    decoded_cp = main._decode_ytdlp(cp1251_bytes)
    check("русские буквы из cp1251 читаются верно",
          "Кривая Кость" in decoded_cp, decoded_cp.splitlines()[0])
    check("в прочитанном тексте нет символа «�»", "�" not in decoded_cp)
    check("разобранные треки сохраняют русское название",
          any("Кривая Кость" in t["title"] for t in main.SearchWorker.parse(decoded_cp)))
    utf8_decoded = main._decode_ytdlp(cyrillic_output.encode("utf-8"))
    check("текст в UTF-8 читается верно", "Кривая Кость" in utf8_decoded,
          utf8_decoded.splitlines()[0])
    check("пустой вывод декодируется без ошибок", main._decode_ytdlp(b"") == "")
    check("строку не портит повторный вызов", main._decode_ytdlp("уже строка") == "уже строка")
    check("окружение просит у yt-dlp UTF-8",
          main._ytdlp_env().get("PYTHONIOENCODING") == "utf-8")

    # --- 2c. Один клик не добавляет, два — добавляют ------------------------
    window = main.SearchWindow(stub_player, "KLEANUP", tracks)
    first_item = window.list.item(0)
    window.list.setCurrentRow(0)
    window.list.itemClicked.emit(first_item)          # одиночный клик
    check("одиночный клик ничего не добавляет",
          not stub_player.playlist_tracks and not stub_player.played,
          str(len(stub_player.playlist_tracks)))
    check("одиночный клик только выделяет трек",
          window.list.currentRow() == 0, str(window.list.currentRow()))
    window.list.itemDoubleClicked.emit(first_item)   # двойной клик
    check("двойной клик добавляет трек в плейлист",
          len(stub_player.playlist_tracks) == 1
          and stub_player.playlist_tracks[0]["url"] == tracks[0]["url"],
          str(len(stub_player.playlist_tracks)))
    check("двойной клик сразу включает трек",
          stub_player.played == tracks[0]["url"], str(stub_player.played))
    check("подсказка окна говорит про двойной клик",
          "Двойной клик" in window.hint.text(), window.hint.text())
    window.list.setCurrentRow(1)
    played_before = stub_player.played
    window.add_current()
    check("кнопка «Добавить» берёт выбранный трек",
          len(stub_player.playlist_tracks) == 2
          and stub_player.playlist_tracks[1]["url"] == tracks[1]["url"],
          str(len(stub_player.playlist_tracks)))
    check("кнопка «Добавить» не включает трек", stub_player.played == played_before,
          str(stub_player.played))
    check("строка в окне читаемая: автор — название [время]",
          main.SearchWindow.describe(tracks[0]).startswith("Peur De Temps 3 — ")
          and "[1:57]" in main.SearchWindow.describe(tracks[0]),
          main.SearchWindow.describe(tracks[0]))
    window.close()

    # --- 3. Кнопки в интерфейсе --------------------------------------------
    player = main.SvyazCloudPlayerCustom()
    check("кнопка «Поиск» есть", hasattr(player, "btn_search")
          and "Поиск" in player.btn_search.text(), player.btn_search.text())
    check("кнопка удаления есть под списком", hasattr(player, "btn_remove_track"))
    check("подсказка поля обещает поиск",
          "название" in player.url_input.placeholderText(), player.url_input.placeholderText())
    check("на старте плейлист пуст (файла не было)",
          len(player.playlist_tracks) == 0, str(len(player.playlist_tracks)))

    # --- 4. Добавление из поиска -------------------------------------------
    player.add_search_result(tracks[0])
    player.add_search_result(tracks[1])
    check("треки из поиска добавлены", len(player.playlist_tracks) == 2,
          str(len(player.playlist_tracks)))
    check("в списке две строки", player.playlist_widget.count() == 2,
          str(player.playlist_widget.count()))
    check("название найденного трека сохранено",
          player.playlist_tracks[0]["title"] == tracks[0]["title"])
    check("автор найденного трека сохранён",
          player.playlist_tracks[0]["author"] == tracks[0]["author"])
    check("ссылка сохранена", player.playlist_tracks[0]["url"] == tracks[0]["url"])

    # --- 5. Файл с ПК попадает в плейлист ----------------------------------
    player.add_search_result({"type": "local", "path": local_wav,
                              "title": "my track", "author": "Локальный файл"})
    check("файл с ПК добавлен", len(player.playlist_tracks) == 3, str(len(player.playlist_tracks)))
    check("путь к файлу сохранён",
          player.playlist_tracks[2].get("path") == local_wav,
          str(player.playlist_tracks[2].get("path")))
    check("у файла с ПК нет ссылки SoundCloud", not player.playlist_tracks[2].get("url"))

    # --- 6. Плейлист сохранён в файл ----------------------------------------
    path = player._playlist_path()
    check("playlist.json создан", os.path.exists(path), path)
    with open(path, "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    check("в файле три трека", len(saved["tracks"]) == 3, str(len(saved["tracks"])))

    # --- 7. Плейлист восстанавливается при новом запуске ---------------------
    player2 = main.SvyazCloudPlayerCustom()
    check("после перезапуска плейлист восстановлен", len(player2.playlist_tracks) == 3,
          str(len(player2.playlist_tracks)))
    check("ссылки восстановлены", player2.playlist_tracks[0]["url"] == tracks[0]["url"])
    check("авторы восстановлены", player2.playlist_tracks[1]["author"] == tracks[1]["author"])
    check("путь файла с ПК восстановлен", player2.playlist_tracks[2]["path"] == local_wav)
    check("список в виджете тоже восстановлен", player2.playlist_widget.count() == 3,
          str(player2.playlist_widget.count()))

    # --- 8. Удаление трека ---------------------------------------------------
    player2.playlist_widget.setCurrentRow(1)
    player2.remove_selected_track()
    check("трек удалён из списка", len(player2.playlist_tracks) == 2, str(len(player2.playlist_tracks)))
    check("виджет тоже потерял строку", player2.playlist_widget.count() == 2)
    check("удалён именно выбранный трек", player2.playlist_tracks[1]["path"] == local_wav,
          str(player2.playlist_tracks[1].get("path")))
    with open(path, "r", encoding="utf-8") as fh:
        check("удаление сразу записано в файл", len(json.load(fh)["tracks"]) == 2)
    check("удаление несуществующего трека безопасно", player2.remove_track_at(99) is False)
    check("список не изменился после неудачного удаления", len(player2.playlist_tracks) == 2)

    # --- 9. Удаление играющего трека ----------------------------------------
    player2.current_index = 0
    player2.remove_track_at(0)
    check("индекс играющего трека сброшен", player2.current_index is None, str(player2.current_index))
    check("после удаления остался один трек", len(player2.playlist_tracks) == 1)

    # --- 10. Воспроизведение локального файла из плейлиста ------------------
    before = len(player2.playlist_tracks)
    player2._open_track_at(0)
    wait(150)
    # Пути сравниваем через normcase: Windows не различает регистр букв
    played = os.path.normcase(os.path.abspath(player2.current_audio_file or ""))
    wanted = os.path.normcase(os.path.abspath(local_wav))
    check("файл с ПК играет из плейлиста",
          player2.is_playing and played == wanted,
          f"играет {played}, ждали {wanted}")
    check("путь в плейлисте совпадает с играющим файлом",
          os.path.normcase(os.path.abspath(player2.playlist_tracks[0].get("path", ""))) == played)
    check("автор локального файла показан", "Локальный" in player2.author_label.text(),
          player2.author_label.text())
    check("воспроизведение не добавило дублей", len(player2.playlist_tracks) == before)

    # --- 11. Переходы по плейлисту не падают на локальных файлах ------------
    player2.play_next_track()
    wait(100)
    check("переход к следующему работает с локальным файлом", player2.is_playing)
    player2.play_prev_track()
    wait(100)
    check("переход к предыдущему работает", player2.is_playing)

    # --- 12. Отсутствующий файл с ПК обрабатывается мягко -------------------
    player2.add_search_result({"type": "local", "path": os.path.join(tmp, "net-takogo.wav"),
                              "title": "пропавший"})
    check("пропавший файл всё равно в списке", len(player2.playlist_tracks) == 2)
    player2._open_track_at(1)
    wait(100)
    check("воспроизведение пропавшего файла не роняет плеер",
          "не найден" in player2.status_lbl.text(), player2.status_lbl.text())

    # --- 13. Пустое поле в поиске и битая ссылка ----------------------------
    player2.url_input.setText("")
    player2.search_soundcloud()
    check("пустой поиск просит ввести название",
          "введите" in player2.status_lbl.text().lower(), player2.status_lbl.text())
    check("добавление пустой ссылки ничего не ломает",
          player2.add_search_result({"type": "sc", "url": ""}) is False)
    check("мусорный трек отклонён", player2._add_track({"title": "без url и пути"}) is False)

    player2.close()
    player.close()
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, errors="replace")
    ok = False
    try:
        ok = main_test()
    except Exception:
        print("ТЕСТ УПАЛ С ИСКЛЮЧЕНИЕМ")
        print(traceback.format_exc())
    passed = sum(1 for _, good, _ in results if good)
    print(f"\nИтог: {passed}/{len(results)} проверок пройдено")
    print(f"EXIT={0 if (ok and passed == len(results)) else 1}")
    sys.exit(0 if (ok and passed == len(results)) else 1)


    # --- 6. playlist.json создан и содержит всё ----------------------------
    path = player._playlist_path()
    check("playlist.json создан", os.path.exists(path), path)
    with open(path, "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    check("в файле три трека", len(saved["tracks"]) == 3, str(len(saved["tracks"])))
    check("ссылка SoundCloud лежит в файле",
          saved["tracks"][0]["url"].startswith("https://soundcloud.com/"))
    check("путь к файлу с ПК лежит в файле", saved["tracks"][2]["path"] == local_wav)
