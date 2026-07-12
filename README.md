# TeleRank

Stop scrolling through thousands of Telegram messages.

TeleRank automatically downloads Telegram channel history, analyzes community reactions, and ranks content so you can instantly find the highest-quality posts.

## Features

- 🚀 One-click Telegram Bot import
- ⚡ Fast download (images + text only, no videos)
- 😊 Reaction-based ranking
- 📈 Community popularity sorting
- 🖼 Full-screen TikTok-style gallery
- 🔍 Emoji filter & matrix grid view
- 📂 Local archive
- 📱 LAN access for mobile viewing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
bash scripts/build_mac_dmg.sh
```

## Config

1. Get Telegram API credentials from https://my.telegram.org
2. Configure via the app's Settings panel
3. Optionally set up a bot token for auto-import

## Download

Get the latest DMG from [Releases](https://github.com/xianyu997/TeleRank/releases).
