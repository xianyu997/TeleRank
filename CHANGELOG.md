# Changelog

## v2026.08.02 — Windows 后台服务与改进

- Windows 后台服务 EXE（`TeleRankService`）：双击即开浏览器，可安装为系统服务
- 定时自动增量同步（`interval_minutes` 生效，状态面板显示下次同步时间）
- 删除导入改为移入 `DeletedImports` 回收目录（可恢复），删除接口仅限本机（安全加固）
- Windows 原生文件夹选择器；修复 jina.ai 搜索链接双重前缀

## v1.0.0 — 2026-07-12

- Initial release
- Telegram Bot auto-import via direct message
- Reaction-based ranking engine
- TikTok-style full-screen gallery with scroll-snap
- Emoji filter chips and matrix grid view
- Multi-channel management with import history
- LAN access for mobile viewing
- Incremental syncing via Telethon user account
- Image + sticker + text download (video/GIF filtered)
- Direct file deletion (rmtree, no trash)
