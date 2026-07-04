# 开发和打包

## 本地运行

```powershell
python tg_reaction_web_launcher.py
```

如果 `1717` 端口被占用，程序会尝试使用后续可用端口。

## 安装打包依赖

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
```

## 打包 Windows EXE

```powershell
.\.venv\Scripts\pyinstaller.exe TGReactionRanker.spec --noconfirm
```

输出文件：

```text
dist\TGReactionRanker_fixed.exe
```

## 发布建议

源码仓库不直接提交 EXE。EXE 更适合上传到 GitHub Releases，README 里放下载链接。
