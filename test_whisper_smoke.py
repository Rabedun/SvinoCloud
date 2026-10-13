"""Оффлайн-проверка новой логики текста песни (Whisper) без интернета и без звуковой карты."""
import os, sys, tempfile, traceback, types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, QEventLoop

import main


class _Tee:
    """Дублирует вывод в консоль и в файл, чтобы результат теста не потерялся."""

    def __init__(self, path):
        self._fh = open(path, "w", encoding="utf-8")

    def write(self, text):
        sys.__stdout__.write(text)
        self._fh.write(text)
        self._fh.flush()

    def flush(self):
        sys.__stdout__.flush()
        self._fh.flush()


results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(("PASS " if condition else "FAIL ") + name + (f"  -> {detail}" if detail else ""))


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(predicate, timeout_ms=6000, step_ms=100):
    """Ждёт условие, прокручивая цикл событий: устойчиво к медленной машине."""
    waited = 0
    while not predicate() and waited < timeout_ms:
        wait(step_ms)
        waited += step_ms
    return bool(predicate())


def main_test():
    app = QApplication(sys.argv)
    import lyrics_web

    # --- 1. Проверка совпадения: текст чужой песни показывать нельзя --------
    # Реальный случай: запрос «в простонародье» находил «Искалу» Земфиры,
    # потому что поиск шёл по любому слову. Теперь такое отсекается.
    cases = [
        ("в простонародье", "KEPACREEP", "Искала", "Земфира", False),
        ("в простонародье", "KEPACREEP", "в простонародье", "KEPACREEP", True),
        ("Flickermood", "Forss", "Flickermood", "Forss", True),
        ("Flickermood", "Forss", "Flickermood", "Другой", False),
        ("Искала", "Земфира", "Искала", "Земфира", True),
        ("Песня", "Исполнитель", "", "Исполнитель", False),
    ]
    for want_title, want_artist, got_title, got_artist, expected in cases:
        got = lyrics_web.matches_track(got_title, got_artist, want_title, want_artist)
        check(f"совпадение: «{got_title}» ищем как «{want_title}» -> {expected}",
              got == expected, str(got))
    check("одинаковое название без указания исполнителя — совпадение",
          lyrics_web.matches_track("Искала", "", "Искала", ""))
    check("название отличается одним словом — не совпадение",
          not lyrics_web.matches_track("Группа крови", "Кино", "Звезда", "Кино"))

    # --- 2. Чистка названий и таймкодов -------------------------------------
    check("чистка убирает (Official Video)",
          lyrics_web.clean_title("Искала (Official Video)") == "Искала",
          repr(lyrics_web.clean_title("Искала (Official Video)")))
    check("чистка убирает [Lyrics]",
          lyrics_web.clean_title("Искала [Lyrics]") == "Искала",
          repr(lyrics_web.clean_title("Искала [Lyrics]")))
    check("чистка убирает feat.",
          "feat" not in lyrics_web.clean_title("Песня feat. Кто-то").lower(),
          repr(lyrics_web.clean_title("Песня feat. Кто-то")))
    check("из «Исполнитель - Название» получаем «Название»",
          "Искала" in lyrics_web.title_variants("Земфира - Искала"),
          str(lyrics_web.title_variants("Земфира - Искала")))
    check("таймкоды вырезаются",
          lyrics_web._plain("[00:12.30] Привет мир") == "Привет мир",
          repr(lyrics_web._plain("[00:12.30] Привет мир")))

    # --- 3. Сеть отключаем: тест проверяет логику, а не интернет -----------
    real_find = lyrics_web.find
    real_enabled = lyrics_web.is_enabled
    web_calls = []

    def fake_find(title, artist="", verbose=False):
        web_calls.append((title, artist))
        return "", ""                       # «в интернете не нашли»

    lyrics_web.find = fake_find
    lyrics_web.is_enabled = lambda: True

    player = main.SvinoklaudPlayer()

    # --- 1. Работа с кэшем .txt рядом с аудиофайлом -------------------------
    tmp = tempfile.mkdtemp(prefix="sc_lyrics_")
    audio = os.path.join(tmp, "track.mp3")
    with open(audio, "wb") as fh:
        fh.write(b"\x00" * 1024)
    check("путь кэша = <трек>.txt",
          player._lyrics_cache_path(audio) == os.path.join(tmp, "track.txt"),
          player._lyrics_cache_path(audio))
    check("пустой путь -> без кэша", player._lyrics_cache_path("") == "")

    player.current_audio_file = audio
    track_txt = os.path.join(tmp, "track.txt")
    with open(track_txt, "w", encoding="utf-8") as fh:
        fh.write("Уже распознанный текст песни")

    # --- 2. Кэш используется раньше распознавания (и без Whisper) -----------
    player.set_now_playing("Тестовый трек", author="Локальный файл")
    check("текст из кэша показан сразу",
          player.lyrics_label._raw_text == "Уже распознанный текст песни",
          repr(player.lyrics_label._raw_text))
    check("поток Whisper не запускался при готовом кэше", player._whisper_thread is None)

    # --- 3. Кэша нет -> поток вежливо сообщает об отсутствии Whisper --------
    # На машине Whisper может быть установлен: подменяем модуль, чтобы шаг был
    # детерминированным и не тянул скачивание модели.
    os.remove(track_txt)
    player._lyrics_memory.clear()
    sys.modules["whisper"] = None       # import whisper -> ImportError
    player.set_now_playing("Второй трек", author="Локальный файл")
    # Путь теперь двухшаговый: сначала поиск в интернете, потом Whisper. Ждём конца.
    wait_until(lambda: "Whisper" in player.status_lbl.text(), 8000)
    check("после поиска в сети дошли до Whisper",
          "Whisper" in player.status_lbl.text() or "не установлен" in player.status_lbl.text(),
          player.status_lbl.text())
    wait_until(lambda: player.lyrics_label._raw_text == main.WHISPER_INSTALL_HINT)
    check("показана команда установки",
          main.WHISPER_INSTALL_HINT in player.lyrics_label._raw_text,
          repr(player.lyrics_label._raw_text))
    check("статус подсказывает лаунчер с правильным Python",
          "Запустить плеер.cmd" in player.status_lbl.text(), player.status_lbl.text())
    wait_until(lambda: len(main.WhisperWorker._alive) == 0)
    check("ссылки на живой поток не осталось", len(main.WhisperWorker._alive) == 0,
          str(len(main.WhisperWorker._alive)))
    sys.modules.pop("whisper", None)

    # --- 4. Результат распознавания: сохраняем .txt и показываем текст ------
    player._on_whisper_transcribed("Второй трек", audio, "первая строка вторая строка")
    check("текст распознавания сохранён в .txt", os.path.exists(track_txt))
    with open(track_txt, "r", encoding="utf-8") as fh:
        saved = fh.read()
    check("содержимое .txt верное", saved == "первая строка вторая строка", repr(saved))
    check("текст распознавания в блоке титров",
          player.lyrics_label._raw_text == "первая строка вторая строка",
          repr(player.lyrics_label._raw_text))

    # ответ по устаревшему треку игнорируется и не портит кэш
    player.lyrics_label.set_scroll_text("актуальный текст")
    audio3_placeholder = os.path.join(tmp, "foreign.mp3")
    player._on_whisper_transcribed("Совсем другой трек", audio3_placeholder, "чужой текст")
    check("ответ по чужому треку не перебивает титры",
          player.lyrics_label._raw_text == "актуальный текст",
          repr(player.lyrics_label._raw_text))
    check("чужой результат не попал в кэш",
          not os.path.exists(os.path.join(tmp, "foreign.txt")))

    # --- 5. Очистка текста от LRC-таймкодов и фильтр мусора ------------------
    cleaned = main.WhisperWorker._clean_text("[00:12.34] Привет   мир\n[01:02]ещё строка")
    check("LRC-таймкоды удалены", cleaned == "Привет мир ещё строка", repr(cleaned))
    check("мусорный ответ Whisper отброшен",
          not main.WhisperWorker._looks_like_speech("§♪♪ ~"))
    check("нормальный текст проходит фильтр",
          main.WhisperWorker._looks_like_speech("Куплет один"))

    # --- 6. Несуществующий файл -> ошибка, а не падение --------------------
    errors = []
    worker = main.WhisperWorker(os.path.join(tmp, "нет_такого.mp3"), "Нет файла")
    worker.failed.connect(lambda title, reason: errors.append(reason))
    worker.start()
    wait(800)
    check("отсутствующий файл -> сообщение об ошибке", errors and "не найден" in errors[0],
          str(errors))

    # --- 7. Полный путь Whisper на подставном модуле (реальный не установлен) ---
    # ffmpeg в тестовой среде может отсутствовать — считаем, что он есть
    original_ensure_ffmpeg = main.WhisperWorker._ensure_ffmpeg
    main.WhisperWorker._ensure_ffmpeg = staticmethod(lambda: True)
    fake = types.ModuleType("whisper")
    state = {"load_calls": [], "options": []}

    class _FakeModel:
        def transcribe(self, path, **options):
            state["path"] = path
            state["options"].append(options)
            if state.get("junk"):                 # инструментал: только шум
                return {"text": "§", "language": "ru", "avg_logprob": -0.1}
            lang = options.get("language")
            # Имитируем промах: автоопределение сказало «uk», русская попытка — точнее
            if lang is None:
                return {"text": "Морюк за кладу", "language": "uk",
                        "avg_logprob": state.get("auto_logprob", -0.9)}
            return {"text": f"текст на языке {lang}", "language": lang,
                    "avg_logprob": state.get("ru_logprob", -0.4)}

    def _fake_load_model(name):
        state["load_calls"].append(name)
        return _FakeModel()

    fake.load_model = _fake_load_model
    sys.modules["whisper"] = fake

    audio2 = os.path.join(tmp, "fake_track.mp3")
    with open(audio2, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player.current_audio_file = audio2
    player.set_now_playing("Трек из Whisper", author="Локальный файл")
    # Поток с подставной моделью живёт мгновение, поэтому судим по результату,
    # а не по наличию поля _whisper_thread.
    wait_until(lambda: bool(state["load_calls"]) and player._whisper_thread is None, 8000)
    check("с Whisper: модель запрошена и она не слабее base",
          state["load_calls"] == [main.WHISPER_MODEL_NAME]
          and main.WHISPER_MODEL_NAME in ("small", "medium"), str(state["load_calls"]))
    check("с Whisper: CPU-режим (fp16=False)", state["options"][0].get("fp16") is False,
          str(state["options"][0]))
    check("с Whisper: первый проход БЕЗ языка — автоопределение",
          "language" not in state["options"][0], str(state["options"][0]))
    check("с Whisper: при автоопределении нет подсказки (чистый выбор языка)",
          "initial_prompt" not in state["options"][0], str(state["options"][0]))
    check("с Whisper: повторы фраз по кругу отключены",
          state["options"][0].get("condition_on_previous_text") is False,
          str(state["options"][0].get("condition_on_previous_text")))
    check("с Whisper: «uk» распознан как сомнительный -> сделан второй проход",
          len(state["options"]) == 2 and state["options"][1].get("language") == "ru",
          f"{[o.get('language') for o in state['options']]}")
    check("с Whisper: второй проход несёт подсказку про выбранный язык",
          "русск" not in (state["options"][1].get("initial_prompt") or "").lower()
          and "ru" in (state["options"][1].get("initial_prompt") or "").lower(),
          repr(state["options"][1].get("initial_prompt")))
    check("с Whisper: выбран вариант с лучшей уверенностью модели (русский)",
          player.lyrics_label._raw_text == "текст на языке ru",
          repr(player.lyrics_label._raw_text))
    check("с Whisper: .txt с текстом сохранён рядом с треком",
          os.path.exists(os.path.join(tmp, "fake_track.txt")))
    check("с Whisper: статус об успехе", "распознан" in player.status_lbl.text(),
          player.status_lbl.text())

    # повторный запуск трека берёт текст из .txt, не загружая модель снова
    player._lyrics_memory.clear()
    player.current_audio_file = audio2
    player.set_now_playing("Трек из Whisper", author="Локальный файл")
    wait(200)
    check("повтор: текст поднят из кэша без нового потока",
          state["load_calls"] == [main.WHISPER_MODEL_NAME] and player._whisper_thread is None,
          str(state["load_calls"]))
    check("повтор: текст в титрах", player.lyrics_label._raw_text == "текст на языке ru",
          repr(player.lyrics_label._raw_text))

    # модель переиспользуется между разными треками
    audio3 = os.path.join(tmp, "fake_track2.mp3")
    with open(audio3, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player.current_audio_file = audio3
    player.set_now_playing("Второй трек Whisper", author="Локальный файл")
    wait_until(lambda: player._whisper_thread is None, 8000)
    check("модель загружена один раз на несколько треков",
          state["load_calls"] == [main.WHISPER_MODEL_NAME], str(state["load_calls"]))

    # --- 7b. Нет ffmpeg -> понятная подсказка вместо тишины -------------------
    main.WhisperWorker._ensure_ffmpeg = staticmethod(lambda: False)
    player._lyrics_memory.clear()
    audio4 = os.path.join(tmp, "no_ffmpeg.mp3")
    with open(audio4, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player.current_audio_file = audio4
    player.set_now_playing("Без ffmpeg", author="Локальный файл")
    wait_until(lambda: player.lyrics_label._raw_text == main.WHISPER_FFMPEG_HINT)
    check("без ffmpeg: подсказка про imageio-ffmpeg в титрах",
          player.lyrics_label._raw_text == main.WHISPER_FFMPEG_HINT,
          repr(player.lyrics_label._raw_text))
    check("без ffmpeg: статус объясняет причину", "ffmpeg" in player.status_lbl.text(),
          player.status_lbl.text())
    check("без ffmpeg: пустой .txt не создаётся",
          not os.path.exists(os.path.join(tmp, "no_ffmpeg.txt")))
    check("без ffmpeg: модель не загружалась зря",
          state["load_calls"] == [main.WHISPER_MODEL_NAME], str(state["load_calls"]))
    main.WhisperWorker._ensure_ffmpeg = staticmethod(original_ensure_ffmpeg)

    # --- 7c-1. Настоящий украинский не переписывается на русский ---------------
    # Модель уверенно говорит «uk» и в варианте на русском уверенность ниже:
    # значит перед нами настоящая украинская песня — оставляем как есть.
    state["options"] = []
    state["auto_logprob"] = -0.2
    state["ru_logprob"] = -0.8
    player._lyrics_memory.clear()
    audio6 = os.path.join(tmp, "uk_track.mp3")
    with open(audio6, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player.current_audio_file = audio6
    player.set_now_playing("Украинский трек", author="Локальный файл")
    # Ждём результат: сначала поиск в сети (ничего не находит), потом распознавание
    wait_until(lambda: player.lyrics_label._raw_text == "Морюк за кладу", 10000)
    check("уверенный «uk» оставлен: перепроверка не ломает чужой язык",
          player.lyrics_label._raw_text == "Морюк за кладу",
          repr(player.lyrics_label._raw_text))
    check("перепроверка всё равно выполнялась (сравнили уверенность)",
          len(state["options"]) == 2, f"{len(state['options'])} проход(а/ов)")
    state["auto_logprob"] = -0.9
    state["ru_logprob"] = -0.4

    # --- 7c. Мусорный ответ («§» на инструментале) не идёт ни в титры, ни в кэш
    state["junk"] = True
    audio5 = os.path.join(tmp, "noise.mp3")
    with open(audio5, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player.current_audio_file = audio5
    player.set_now_playing("Шум без слов", author="Локальный файл")
    wait_until(lambda: "не распознан" in player.status_lbl.text())
    check("мусорный ответ -> статус «не распознан»",
          "не распознан" in player.status_lbl.text(), player.status_lbl.text())
    check("мусорный ответ не попал в .txt",
          not os.path.exists(os.path.join(tmp, "noise.txt")))

    # отмена: смена трека до распознавания отключает устаревший поток
    stale = main.WhisperWorker(audio2, "Устаревший трек")
    stale.start()
    stale.requestInterruption()
    stale.wait(2000)
    check("запрошенная отмена не оставляет живой поток", not stale.isRunning())

    # --- 7d. Текст нашли в интернете: Whisper не запускается вовсе -------------
    lyrics_web.find = lambda title, artist="", verbose=False: (
        "Текст из интернета про эту песню", "lrclib")
    player._lyrics_memory.clear()
    audio7 = os.path.join(tmp, "web_track.mp3")
    with open(audio7, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player.current_audio_file = audio7
    player.set_now_playing("Трек из интернета", author="Известный исполнитель")
    wait_until(lambda: player.lyrics_label._raw_text == "Текст из интернета про эту песню",
               8000)
    check("интернет: текст показан в титрах",
          player.lyrics_label._raw_text == "Текст из интернета про эту песню",
          repr(player.lyrics_label._raw_text))
    check("интернет: Whisper не запускался", player._whisper_thread is None)
    check("интернет: текст сохранён в .txt",
          os.path.exists(os.path.join(tmp, "web_track.txt")))
    check("интернет: в статусе назван источник",
          "интернете" in player.status_lbl.text() and "lrclib" in player.status_lbl.text(),
          player.status_lbl.text())

    # --- 7e. Ответ поиска по устаревшему треку не должен ничего портить ------
    player.lyrics_label.set_scroll_text("текст актуального трека")
    audio8 = os.path.join(tmp, "old_web.mp3")
    with open(audio8, "wb") as fh:
        fh.write(b"\x00" * 1024)
    player._on_lyrics_web_found("Совсем другой трек", audio8, "чужой текст из сети", "lrclib")
    check("интернет: чужой ответ не перебивает титры",
          player.lyrics_label._raw_text == "текст актуального трека",
          repr(player.lyrics_label._raw_text))
    check("интернет: чужой ответ не попал в кэш",
          not os.path.exists(os.path.join(tmp, "old_web.txt")))

    # --- 7f. Поиск в интернете выключен -> сразу Whisper ---------------------
    lyrics_web.find = fake_find
    lyrics_web.is_enabled = lambda: False
    player._lyrics_memory.clear()
    audio9 = os.path.join(tmp, "no_web.mp3")
    with open(audio9, "wb") as fh:
        fh.write(b"\x00" * 1024)
    # Поток распознавания живёт мгновение, поэтому следим за вызовом, а не за полем
    started = []
    real_start = player._start_whisper
    player._start_whisper = lambda path, title: (started.append((path, title)),
                                                 real_start(path, title))[1]
    player.current_audio_file = audio9
    player.set_now_playing("Без интернета", author="Локальный файл")
    wait_until(lambda: bool(started), 8000)
    check("интернет выключен: сразу запускается Whisper", bool(started), str(started))
    player._start_whisper = real_start
    player._stop_whisper_thread()
    player._stop_web_thread()
    lyrics_web.find = real_find
    lyrics_web.is_enabled = real_enabled

    # --- 8. Очередь кэша и завершение работы ------------------------------
    player._ui_queue.put(("cached", "", "", "", ""))
    player._drain_ui_queue()
    check("очередь кэша не падает на пустом URL", True)

    player._stop_whisper_thread()
    player.close()      # проверяем closeEvent
    check("closeEvent прошёл без исключения", True)

    failed = [r for r in results if not r[1]]
    print(f"\nИтог: {len(results) - len(failed)}/{len(results)} проверок пройдено")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout = _Tee(os.path.join(os.path.dirname(os.path.abspath(__file__)), "whisper_smoke.log"))
    try:
        code = main_test()
    except Exception:
        traceback.print_exc()
        code = 2
    print(f"EXIT={code}")
    sys.stdout.flush()
    sys.exit(code)
