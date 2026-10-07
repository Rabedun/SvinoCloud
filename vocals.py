"""Отделение вокала от музыки (Demucs) — без этого Whisper слышит кашу.

Модель обучена на чистой речи, а в песне голос поёт поверх инструментов.
Мы один раз отделяем вокал в отдельный файл и скармливаем Whisper уже чистый
голос: качество текста становится в разы лучше.
"""
import os
import glob
import shutil
import subprocess


def _cache_dir():
    """Папка с разделёнными дорожками — рядом с кэшем плеера."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".sc_cache", "vocals")


def is_available():
    """Есть ли Demucs в этом Python."""
    try:
        import demucs  # noqa: F401
        return True
    except Exception:
        return False


def _newest_vocals_for(base_name):
    """Уже готовый вокал для трека (чтобы не считать второй раз)."""
    folder = _cache_dir()
    for path in sorted(glob.glob(os.path.join(folder, base_name + ".*vocals*.wav")),
                       key=os.path.getmtime, reverse=True):
        if os.path.getsize(path) > 0:
            return path
    return ""


def _run_demucs(audio_path, model_name, progress=None):
    """Запускаем Demucs и возвращаем путь к файлу с вокалом."""
    if not shutil.which("ffmpeg"):
        try:
            import imageio_ffmpeg
            folder = os.path.join(_cache_dir(), "bin")
            os.makedirs(folder, exist_ok=True)
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            shutil.copyfile(exe, os.path.join(folder, "ffmpeg.exe"))
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            return ""
    cmd = [os.sys.executable, "-m", "demucs", "--two-stems=vocals",
           "-n", model_name, "--out", _cache_dir(), audio_path]
    if progress:
        progress("Demucs: отделяем голос от музыки…")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Demucs не отработал: {exc}")
        return ""
    if proc.returncode != 0:
        print(f"Demucs вернул код {proc.returncode}: {(proc.stderr or '')[-300:]}")
        return ""
    return _newest_vocals_for(os.path.splitext(os.path.basename(audio_path))[0])


def prepare_vocals(audio_path, model_name="htdemucs", progress=None):
    """Путь к файлу с вокалом: из кэша или свежий расчёт.

    Возвращаем путь исходного трека, если Demucs недоступен или не сработал:
    лучше распознать с музыкой, чем не показать текст вовсе.
    """
    if not audio_path or not os.path.exists(audio_path):
        return audio_path
    cached = _newest_vocals_for(os.path.splitext(os.path.basename(audio_path))[0])
    if cached:
        if progress:
            progress("Вокал уже отделён ранее")
        return cached
    if not is_available():
        if progress:
            progress("Demucs не установлен — распознаём как есть")
        return audio_path
    vocals = _run_demucs(audio_path, model_name, progress)
    return vocals or audio_path
