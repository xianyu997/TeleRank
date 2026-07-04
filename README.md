# TG Reaction Ranker

Telegram 表情反馈排行查看器。把 Telegram Desktop 导出的聊天记录导入后，软件会按消息收到的表情反馈数量排行，并用大图观看页和矩阵页展示图片消息。

## 下载

普通用户直接下载 EXE 即可：

[下载 TGReactionRanker.exe](https://github.com/xianyu997/tg-reaction-ranker/releases/download/v2026.07.04-auto-links/TGReactionRanker.exe)

运行后会自动打开本地网页界面。软件本体是本地程序，不需要部署服务器。

## 它解决什么问题

Telegram 导出文件里有很多图片和消息，人工翻找很慢。这个工具会做三件事：

1. 读取 Telegram Desktop 导出的 `ChatExport_*` 文件夹。
2. 按每条消息的表情反馈数量排序。
3. 把图片作为主体展示，方便快速浏览高反馈内容。

## 主要功能

- 按表情反馈数量排行消息。
- 支持导入包含 `messages*.html` 和 `photos` 的完整导出文件夹。
- 自动把导入内容归档到 `D:\TelegramReactionRanker\Imports`。
- 导入历史会保留在页面里，后续可以一键重新打开。
- 查看页是大图滑动浏览，矩阵页适合快速扫图。
- 自动识别 Telegram 消息链接。
- 无法识别公开消息直链时，会退回到导出内容里的频道入口或邀请链接。
- 支持黑色主题和白色主题。

## 使用流程

1. 在 Telegram Desktop 里导出频道或聊天记录。
2. 导出时尽量包含图片文件。
3. 打开 `TGReactionRanker.exe`。
4. 在导入页选择或粘贴导出文件夹路径。
5. 进入用户观看页或矩阵页浏览排行结果。
6. 点击“复制链接”或“跳转”复制识别到的 Telegram 链接。

## 关于链接识别

Telegram 导出文件不一定包含公开消息直链。软件会按这个顺序处理：

1. 如果能确认频道和消息 ID，就生成 `https://t.me/频道/消息ID`。
2. 如果导出里只有邀请链接或频道入口，就使用最可信的入口链接。
3. 如果导出里完全没有可用 Telegram 链接，页面会显示无链接。

这样做是为了避免把消息正文里 @ 其他频道的广告链接误当成当前频道链接。

## 项目结构

```text
tg_reaction_web_launcher.py   本地 HTTP 服务和文件扫描逻辑
tg_reaction_web.html          网页交互界面
TGReactionRanker.spec         PyInstaller 打包配置
docs/                         使用说明、打包说明、常见问题
```

## 从源码运行

需要 Python 3：

```powershell
python tg_reaction_web_launcher.py
```

默认会在本机 `127.0.0.1:1717` 附近选择可用端口，并打开浏览器。

## 重新打包 EXE

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pyinstaller.exe TGReactionRanker.spec --noconfirm
```

生成文件在 `dist\TGReactionRanker_fixed.exe`。发布时可以重命名为 `TGReactionRanker.exe`。

## 隐私说明

软件只读取你本机选择的 Telegram 导出文件夹。导入后的副本默认保存在 `D:\TelegramReactionRanker\Imports`，不会主动上传聊天记录或图片。

## 说明

这不是 Telegram 官方工具。Telegram 导出格式如果发生变化，解析逻辑可能需要同步更新。
