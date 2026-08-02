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

## Windows 后台服务

本项目同时提供 Windows 版本：把 Web 界面和 Telegram 机器人/频道导入打包成一个可双击
运行、也可安装为系统服务（开机自启、无需登录桌面）的 EXE。相关文件在
[`TeleRankService/`](TeleRankService/) 目录。

### 双击即用（无需安装）

直接双击 `TeleRankService\TeleRankService.exe`：

- 自动启动本地服务，并用默认浏览器打开 `http://127.0.0.1:1717/`
- 如果后台服务已在运行，双击只会打开浏览器，不会重复启动
- 系统服务模式保持无窗口后台运行，不自动弹浏览器

### 构建 EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\TeleRankService\build_service.ps1
```

产物 `TeleRankService.exe` 需要与旁边的 `_internal` 文件夹一起使用。

### 安装为系统服务

```powershell
powershell -ExecutionPolicy Bypass -File .\TeleRankService\install_service.ps1
```

- 服务名 `TeleRankService`，开机自启，默认端口 1717
- 配置/Telegram 会话：`C:\ProgramData\TelegramReactionRanker`
- 导入数据：`D:\TelegramReactionRanker\Imports`，删除的导入进入 `DeletedImports` 回收目录（可恢复）
- 配置 Telegram 账号并登录后，会按设定间隔自动增量同步；局域网设备可访问
- 卸载：`.\TeleRankService\uninstall_service.ps1`（配置和登录会话会保留）

## 开发与测试

后端提供不依赖 Telegram 凭据/网络的离线冒烟测试：

```bash
python -m unittest discover -s tests -v
```
