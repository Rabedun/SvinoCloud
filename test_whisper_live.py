"""Живая проверка текста песни: реальный Whisper на реальном треке из кэша плеера.

Запуск:  python test_whisper_live.py
Нужны openai-whisper и ffmpeg (проще всего: pip install openai-whisper imageio-ffmpeg).
При первом запуске модель tiny один раз скачается (~75 МБ), дальше всё офлайн.
Трек берётся из локального кэша .sc_cache — интернет для самого распознавания не нужен.
"""
import glob
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

import main


def wait_until(predicate, timeout_ms=900000, step_ms=250):
    """Крутит цикл событий плеера, пока условие не выполнится или не выйдет время."""
    waited = 0
    while not predicate() and waited < timeout_ms:
        loop = QEventLoop()
        QTimer.singleShot(step_ms, loop.quit)
        loop.exec()
        waited += step_ms
    return predicate()


def pick_track(argv):
    """Трек для проверки: путь из аргумента, иначе самый крупный mp3 в кэше плеера."""
    if len(argv) > 1 and argv[1]:
        return argv[1]
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sc_cache")
    files = glob.glob(os.path.join(cache, "*.mp3"))
    return max(files, key=os.path.getsize) if files else ""


def run():
    sys.stdout.reconfigure(line_buffering=True, errors="replace")   # консоль Windows любит cp1251
    try:
        import whisper  # noqa: F401
    except Exception as exc:
        print(f"SKIP: Whisper не установлен ({exc})\n{main.WHISPER_INSTALL_HINT}")
        return 2
    if not main.WhisperWorker._ensure_ffmpeg():
        print(f"SKIP: ffmpeg недоступен\n{main.WHISPER_FFMPEG_HINT}")
        return 2
    track = pick_track(sys.argv)
    if not track or not os.path.exists(track):
        print("SKIP: не найден mp3 — укажите путь аргументом или включите трек в плеере")
        return 2

    cache_path = os.path.splitext(track)[0] + ".txt"
    if os.path.exists(cache_path):
        os.remove(cache_path)          # проверяем именно распознавание, а не кэш
    print(f"Трек: {os.path.basename(track)} ({os.path.getsize(track) / 1e6:.1f} МБ)")
    print(f"Модель: {main.WHISPER_MODEL_NAME} (первый запуск скачает её один раз)")
    print(f"Язык: {main.WHISPER_LANGUAGE or 'автоопределение'}")

    app = QApplication(sys.argv)      # ссылку держим: иначе Qt-приложение соберёт GC
    player = main.SvyazCloudPlayerCustom()
    player.current_audio_file = track
    started = time.time()
    player.set_now_playing("Живая проверка", author="Локальный файл")   # тут и стартует Whisper

    ok = wait_until(lambda: os.path.exists(cache_path))
    spent = time.time() - started
    if not ok:
        print(f"FAIL: текста нет за {spent:.0f} с; статус: {player.status_lbl.text()}")
        return 1

    with open(cache_path, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    words = text.split()
    if len(words) >= 5:
        print(f"OK: распознано за {spent:.0f} с, слов: {len(words)}")
    else:
        print(f"WARN: за {spent:.0f} с слов мало ({len(words)}) — похоже, в треке нет вокала; "
              "сама цепочка ffmpeg + Whisper при этом работает")
    print("Статус: " + player.status_lbl.text())
    print("Фрагмент: " + (text[:300] + ("…" if len(text) > 300 else "")))
    print("Кэш текста: " + cache_path)
    return 0


if __name__ == "__main__":
    sys.exit(run())
