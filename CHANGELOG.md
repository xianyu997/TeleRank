# Changelog

## v2026.08.02-layout-tweak — 导入页布局调整

- 「已导入频道」列表移到导入页最前面（状态栏 + 频道列表在上，导入工具在下）

## v2026.08.02-simplified-ui — 界面简化

- 导入入口收敛：频道弹窗移除重复的「粘贴链接快速导入」，只保留手动频道设置
- 历史列表：整行可点击加载频道；移除拖拽排序手柄与单独的导入按钮（保留收藏/删除）；清理拖拽相关死代码
- Telegram 设置弹窗：API/同步参数折叠进「高级设置」（Bot 用户无需展开）
- 顶栏：语言/主题/Telegram 同步/频道链接/导出 CSV 收进一个 ⚙ 设置菜单，顶栏只留页面导航
- 移除 importNote 死代码，导入结果信息并入状态栏
- 新增「未连接服务」提示条（file:// 直接打开或服务未运行时可见）

## v2026.08.02-motion-polish — UI 与动效打磨

- 模态框/灯箱：遮罩淡入淡出 + 关闭时面板滑出缩放（Esc/背板/按钮关闭路径统一）
- 页面切换：增加退出淡出动画（快速连点有保护）
- 导入解析：逐文件读取/解析并显示进度（"正在读取 3/40…"）
- 移动端底部导航：补充按压缩放反馈
- 画廊/矩阵滚动容器：`overscroll-behavior` 硬化（减少 iOS 跳动概率）

## v2026.08.02-ux-polish — 用户体验打磨

- 远程/局域网客户端隐藏「关闭应用」按钮（原本点了无效）
- 「浏览…」选文件夹失败时显示后端提示（服务模式引导手动输入路径）
- 双击 EXE 启动失败时弹出错误对话框并提示日志位置
- Bot：超过 10 分钟的导入不再中途放弃，完成后自动通知；登录提示补充「重新发送链接」
- Windows 文件夹选择器不再闪现控制台窗口
- 前端增加全局 JS 错误兜底（页面出错/操作失败可见提示）

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
