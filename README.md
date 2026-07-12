# TG Reaction Ranker

Rank Telegram channel messages by reaction count. Import HTML/JSON exports or sync via Telethon.

## Features
- TikTok-style full-screen gallery with scroll-snap
- Emoji reaction filter & matrix grid view  
- Telegram bot auto-import
- LAN access for mobile viewing
- Multi-channel management

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
