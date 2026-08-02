# Changelog

## v2026.08.02-hardening — 加固与健壮性

- 保存偏好设置接口仅限本机（安全加固）
- Bot：修复 `t.me/s/` 预览链接提取、URL 中 bot 账号排除、导入并发限制与去重
- 同步：无新增消息时不再重写 `result.json`；长同步期间定时落盘检查点
- 导入扫描支持 `TELERANK_OFFLINE` 离线模式；频道标题查询结果落盘缓存
- 双击启动可发现回退端口上的运行实例；`--status` 输出到文件；`stop_service.ps1` 更健壮
- 新增离线自动化测试（`python -m unittest discover -s tests -v`）

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
