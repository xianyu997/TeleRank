# TeleRank

**Turn massive Telegram channels into reaction-ranked archives. Find the best content in minutes, not hours.**

TeleRank downloads channel history through a Telegram Bot, analyzes community reactions, and ranks posts so you instantly discover the highest-quality content — curated by real people, not algorithms.

## How It Works

```
📎 Send channel link to Bot  →  ⚡ Auto-download (images + text)  →  😊 Rank by reactions  →  🎬 Browse in TikTok-style gallery
```

## Features

- 🚀 One-click Telegram Bot import — just send a channel link
- ⚡ Fast local archive — images, stickers, and text, no video bloat
- 😊 Reaction-based ranking — community curates the content
- 🎬 Full-screen gallery — swipe through top posts like TikTok
- 🔍 Emoji filter & matrix grid view
- 📱 LAN access — browse on your phone from anywhere at home
- 🔒 All data stored locally — nothing in the cloud

## Get Started

[![Download DMG](https://img.shields.io/badge/Download-DMG-blue)](https://github.com/xianyu997/TeleRank/releases/latest)

```bash
# Or build from source
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
bash scripts/build_mac_dmg.sh
```

## Config

1. Get Telegram API credentials from [my.telegram.org](https://my.telegram.org)
2. Open the app → Settings → enter API ID and Hash
3. Create a Bot via [@BotFather](https://t.me/BotFather) and paste the token
4. Send a channel link to your bot → auto-download begins

## Why TeleRank?

Telegram channels have thousands of posts. But the community has already done the curation — every reaction is a vote. TeleRank extracts that collective signal and shows you what matters.

**Stop scrolling. Start ranking.**
