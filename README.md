# TG Reaction Ranker

Telegram export reaction ranking viewer.

This Windows app imports Telegram Desktop chat export folders, ranks messages by emoji reactions, and presents photo-heavy messages in a TikTok-style viewer and a matrix gallery.

## Features

- Import Telegram Desktop `ChatExport_*` folders.
- Automatically archive imports under `D:\TelegramReactionRanker\Imports`.
- Automatically detect channel names and Telegram links when the export contains enough information.
- Copy the detected message or channel link and open Telegram Web saved messages.
- View messages as large image reels or a dense responsive matrix.
- Supports dark and light themes.
- Includes a ready-to-run Windows EXE in `dist/TGReactionRanker.exe`.

## Use

1. Run `dist/TGReactionRanker.exe`.
2. Paste or select a Telegram Desktop export folder.
3. Open the viewer or matrix page.
4. Use `复制链接` or `跳转` to copy the detected Telegram link.

## Build From Source

Install PyInstaller in a virtual environment, then run:

```powershell
pyinstaller TGReactionRanker.spec --noconfirm
```

The packaged app is written as a local Python HTTP server plus a browser-based interface.

## Notes

Telegram private exports do not always contain a public message URL. When a message-level public link cannot be inferred, the app falls back to the best channel entry or invite link found in the export.
