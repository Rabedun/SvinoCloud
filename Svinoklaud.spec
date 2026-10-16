# -*- mode: python ; coding: utf-8 -*-
# Спецификация сборки Свиноклауда в один exe (PyInstaller).
#
# Собрать:  python -m PyInstaller Svinoklaud.spec
# Готовый exe:  dist\Свиноклауд.exe
#
# Важно про Whisper: torch весит ~2 ГБ, поэтому в exe его НЕ кладём.
# Всё остальное (поиск, воспроизведение, кастомизация) работает без него.
# Распознавание текста в exe покажет подсказку — как и задумано в main.py.

import os

# yt-dlp кладём рядом: плеер зовёт его как внешний процесс (yt-dlp.exe).
ytdlp_exe = os.path.abspath('yt-dlp.exe')
binaries = [(ytdlp_exe, '.')] if os.path.isfile(ytdlp_exe) else []

# ffmpeg нужен плееру: SoundCloud отдаёт m4a/AAC, а pygame умеет только mp3 —
# без конвертации такие треки не играют. Ищем свой ffmpeg.exe рядом с проектом,
# иначе берём бинарник из imageio-ffmpeg (он уже есть в окружении Whisper'а).
ff_src = os.path.abspath('ffmpeg.exe')
if not os.path.isfile(ff_src):
    try:
        import imageio_ffmpeg
        ff_src = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ff_src = ''
if ff_src and os.path.isfile(ff_src):
    binaries.append((ff_src, '.'))

# Модули, которые тащить в exe не нужно: они тяжёлые и опциональные.
# torch/numba/tiktoken — зависимости Whisper; без них плеер работает,
# только распознавание текста отключено (плеер сам об этом честно скажет).
# imageio_ffmpeg (83 МБ ffmpeg) — нужен только Whisper'у, поэтому не кладём.
EXCLUDE = [
    'torch', 'numba', 'tiktoken', 'whisper', 'demucs',
    'imageio_ffmpeg', 'vocals',
    'matplotlib', 'scipy', 'pandas', 'IPython', 'notebook',
    'pytest', 'PyInstaller',
]

# Данные, которые нужно упаковать внутрь: pygame и его шрифты.
from PyInstaller.utils.hooks import collect_data_files
datas = []
for pkg in ('pygame', 'pygame-ce'):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=['lyrics_web', 'updater'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDE,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Свиноклауд',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # без чёрного окна консоли
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',      # если появится файл иконки — раскомментируйте
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Свиноклауд',
)
