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
- **🎤 Local lyrics (karaoke)** — lyrics are recognized on your machine with
  **OpenAI Whisper** — no internet, works offline. Online lyrics are tried first,
  Whisper is the fallback. Lyrics scroll smoothly and highlight in sync with the music.
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
   pip install openai-whisper imageio-ffmpeg     # optional, for local lyrics
   ```

3. **Run the player:**
   ```bash
   python main.py
   ```
   On Windows the easiest way is to double-click **`Запустить плеер.cmd`** — it picks
   the correct Python (the one that has Whisper) and installs missing packages.

On first lyrics recognition the `base` Whisper model is downloaded once (~145 MB);
after that everything works offline.

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
| `vocals.py` | Optional vocal isolation before Whisper (off by default) |
| `Запустить плеер.cmd` | Windows launcher (picks the right Python) |
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
