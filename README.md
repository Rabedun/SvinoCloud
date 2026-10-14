# 🎵 Svinoklaud (Свиноклауд)

A desktop music player built with **Python + PyQt6** that searches for music across
several platforms at once, plays local files, recognizes song lyrics **locally with
OpenAI Whisper** (no internet required), and updates itself from GitHub.

> 🇷🇺 Интерфейс и подсказки — на русском. This README describes the same features in English.

---

## ✨ Features

- **🔍 Multi-platform search** — type a song name, and every platform answers in
  parallel. Each result shows its source, e.g. `Serj Tankian — Save Us (SoundCloud)`.
- **🎧 Play any link** — add tracks to the playlist by URL from SoundCloud, YouTube,
  Yandex.Music, VK Music, or Spotify.
- **📥 Playlist import by URL** — pull an entire playlist (VK / SoundCloud /
  Yandex.Music) with one button.
- **💾 Playlists** — the playlist is saved automatically to `playlist.json`, plus
  multiple named playlists with export/import (JSON / M3U).
- **🎤 Lyrics (karaoke)** — lyrics are looked up online (Genius / lrclib /
  lyrics.ovh). Local speech recognition with **OpenAI Whisper is temporarily
  disabled** (it needs ~2 GB of torch/model data; flip `LYRICS_WHISPER_ENABLED`
  back to `True` to re-enable it). Lyrics scroll smoothly and highlight in sync.
- **📊 Waveform & timeline** — animated waveform, seek bar with current / total time,
  Next / Previous / Repeat buttons, and a volume slider.
- **⚙️ Deep customization** — pick a color for every button, choose a font, adjust
  transparency, set a photo/GIF background, and save it all as theme profiles.
- **⬇ Self-update** — checks GitHub for a new release and installs it from within the
  player (your playlist, theme, and tokens are kept).

---

## 🚀 Quick start

1. **Install Python 3.10+** from [python.org](https://www.python.org/downloads/).

2. **Install dependencies** (into the *same* Python that runs the player):
   ```bash
   pip install PyQt6 pygame yt-dlp
   pip install openai-whisper imageio-ffmpeg     # only if you re-enable Whisper lyrics
   ```

3. **Run the player:**
   ```bash
   python main.py
   ```
   On Windows the easiest way is to double-click **`Запустить плеер.cmd`** — it picks
   a suitable Python and installs missing packages.

> Local speech recognition (Whisper / torch) is **currently disabled** — lyrics are
> fetched online only. To turn it back on, set `LYRICS_WHISPER_ENABLED = True` in
> `main.py` and install the two optional packages above.

---

## 📦 Windows build (.exe, no Python needed)

To ship the player to someone without Python, build a standalone Windows bundle:

```bash
pip install pyinstaller
# yt-dlp.exe (standalone binary from https://github.com/yt-dlp/yt-dlp/releases)
# must sit next to the sources — it is packed into the bundle for SoundCloud/YouTube.
python -m PyInstaller Svinoklaud.spec --noconfirm --clean
```

Or simply double-click **`Собрать exe.cmd`** on Windows. The result is
`dist\Свиноклауд\` — a folder containing `Свиноклауд.exe`; zip it and distribute.

Notes about the exe build:

- The bundle runs `yt-dlp.exe` as an external process (found in `_internal`);
  the player picks it automatically when `sys.frozen` is set.
- **Whisper / torch are intentionally excluded** (~2 GB would exceed reasonable
  download size) — the exe keeps every feature except local speech recognition
  and honestly shows a hint about it. Lyrics still work online (Genius / lrclib).
- Playlist, theme, and tokens are stored **next to the exe** (`playlist.json`,
  `.sc_cache\`), not in a temp folder, so updates keep your data.

---

## 🎧 Platforms

| Platform | Search | Playback |
|----------|:------:|:--------:|
| **SoundCloud** | ✅ | ✅ (via yt-dlp, `scsearch`) |
| **YouTube**    | ✅ | ✅ (via yt-dlp, `ytsearch`) |
| **Yandex.Music** | ✅ | ⚠️ full tracks need an OAuth token (previews otherwise) |
| **VK Music**   | ⚠️ needs a token | ✅ (direct MP3 from the VK API) |
| **Spotify**    | via link | ⚠️ metadata only (DRM — no audio) |

Each platform's limitations are reported honestly in the app — nothing is faked.

---

## 🔑 Optional tokens

Enter them in **Settings (⚙️) → the matching tab**; they are stored locally only.

- **Yandex.Music OAuth** — unlocks full-length tracks (instead of 30–90 s previews).
- **VK access token** — enables VK Music search (can also be fetched automatically
  with login + password; the password is never saved).
- **Genius token** — improves online lyric accuracy (fallback: lrclib / lyrics.ovh).

---

## 🛠️ Tech stack

- **PyQt6** — interface
- **pygame** — audio playback & waveform
- **yt-dlp** — SoundCloud / YouTube search & download
- **OpenAI Whisper** — local speech-to-text for lyrics
- Standard library only for updates, playlist I/O, and platform APIs — no extra deps.

---

## 📁 Files

| File | Purpose |
|------|---------|
| `main.py` | The player (interface + logic) |
| `updater.py` | GitHub self-update engine |
| `lyrics_web.py` | Online lyrics lookup (Genius / lrclib / lyrics.ovh) |
| `vocals.py` | Vocal isolation before Whisper (unused — Whisper disabled) |
| `Svinoklaud.spec` | PyInstaller recipe for the standalone Windows build |
| `Запустить плеер.cmd` | Windows launcher (picks the right Python) |
| `Собрать exe.cmd` | One-click PyInstaller build (needs Python + PyInstaller) |
| `Собрать релиз.cmd` | Builds `Svinoklaud.zip` for a GitHub release |
| `Сделать коммит.cmd` | One-click git commit helper |

Your personal data (`playlist.json`, `theme_config.json`, tokens) is kept local and
never uploaded.

---

## ⚠️ Notes

- Lyrics recognition is **speech recognition, not a dictionary** — most words are
  correct, but occasionally a word may be off. Online lyrics are tried first for that
  reason.
- Downloads / recognition run in background threads, so the UI never freezes.
- Yandex.Music and VK use their own APIs directly because yt-dlp's extractors for
  them are broken / limited.

---

Made with ❤️ and PyQt6. Interface in Russian.
