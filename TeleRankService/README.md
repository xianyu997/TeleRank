# TG Reaction Ranker - Windows 后台服务

把本项目的 Web 界面 + Telegram 机器人/频道导入改造成一个真正的 Windows 后台服务（SCM 管理、开机自启、无需登录桌面也能运行）。

## 文件

| 文件 | 作用 |
| --- | --- |
| `windows_service.py` | 服务入口（HTTP 服务 + bot 恢复 + 事件日志/文件日志） |
| `TeleRankService.spec` | PyInstaller 打包配置（onedir） |
| `build_service.ps1` | 一键构建（自动创建 `.venv-build` 并安装依赖） |
| `install_service.ps1` | 安装并启动服务（自动请求管理员权限） |
| `uninstall_service.ps1` | 停止并卸载服务 |

## 构建

```powershell
powershell -ExecutionPolicy Bypass -File .\build_service.ps1
```

产物：`TeleRankService.exe`（连同旁边的 `_internal` 文件夹一起使用，不能只拷单个 exe）。
构建脚本会把这个产物自动同步到本文件夹根目录。

## 双击运行（打开即用，无窗口）

直接双击 `TeleRankService.exe`：**不会弹出终端窗口**，会在后台静默启动本地服务，
并自动用默认浏览器打开 `http://127.0.0.1:1717/`。如果服务已经在后台运行
（开机自启的那种），双击只是打开浏览器，不会重复启动。

- 手动启动的实例停止方式：运行 `stop_service.ps1`（或 `sc.exe stop TeleRankService`）
- 安装为系统服务后保持无窗口运行，不会自动弹浏览器；只有手动打开 EXE 才弹
- 无网络/慢网络环境可设置 `TELERANK_OFFLINE=1` 禁用导入时的频道句柄网页查询
- `--status` 会把状态写到 EXE 旁边的 `TeleRankService-status.txt`（无窗口下可读）
- `stop_service.ps1` 支持自定义数据目录/回退端口（自动探测正在运行的实例）

## 安装 / 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\install_service.ps1
```

会弹出 UAC，然后：

- 创建服务 `TeleRankService`（自动启动）
- 写入参数：`Port=1717`、`DataDir=C:\ProgramData\TelegramReactionRanker`、`ArchiveRoot=D:\TelegramReactionRanker\Imports`、`TrashRoot=D:\TelegramReactionRanker\DeletedImports`
- 添加防火墙入站规则，局域网设备可访问
- 启动服务并输出状态

访问 `http://127.0.0.1:1717/`；局域网地址见 `C:\ProgramData\TelegramReactionRanker\service-info.json`。

## 常用命令

```powershell
sc.exe query TeleRankService            # 状态
sc.exe stop TeleRankService             # 停止
sc.exe start TeleRankService            # 启动
.\TeleRankService.exe --status
.\TeleRankService.exe --run     # 前台模式（等同于双击，也会打开浏览器）
.\stop_service.ps1              # 停止手动启动的后台实例（服务模式用 sc.exe stop）

## 手机远程控制（Telegram Bot）

在 Telegram 里打开 Bot（@Tir10057bot），发送以下命令即可远程控制：

| 命令 | 作用 |
| --- | --- |
| `/help` | 显示命令列表 |
| `/status` | 服务/账号/下载状态 |
| `/channels` | 已导入频道列表 |
| `/download <链接>` | 下载指定频道 |
| `/stop` | 停止当前下载 |

直接发送频道链接（或转发）也会自动下载。Bot 只响应你自己的 Telegram 账号
（通过登录账号自动识别），其他人无法使用。
```

## 卸载

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_service.ps1
```

配置文件/会话（Telegram 登录、bot token）保留在 DataDir，重装后无需重新登录。

## 说明

- 服务默认以 `LocalSystem` 运行，数据放在 `C:\ProgramData\TelegramReactionRanker`，不依赖登录用户。
- 端口被占用时服务会自动换空闲端口（记录到 `service.log`）。
- 删除导入会移入 `TrashRoot`（DeletedImports）而不是永久删除，可恢复。
- 配置 Telegram 账号并登录后，会按 `interval_minutes` 自动增量同步（状态弹窗显示下次同步时间）。
- 日志：`C:\ProgramData\TelegramReactionRanker\service.log` + Windows 事件日志（来源 TG Reaction Ranker Service）。
- 若要改端口/数据目录/回收目录，可改注册表 `HKLM\SYSTEM\CurrentControlSet\Services\TeleRankService\Parameters` 后重启服务，或重新运行安装脚本传参（`-Port` / `-DataDir` / `-ArchiveRoot` / `-TrashRoot`）。
