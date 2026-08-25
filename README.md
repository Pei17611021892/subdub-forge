# StoryCut

StoryCut 是一款 Windows AI 解说剪辑工具，用于把较长的视频整理为 3 分钟以内、适合 YouTube Shorts 发布的英文解说成片。

仓库从 `2.0.0` 起只维护一个应用。原来的一比一翻译 V1 已退役，其中仍适合当前工作流的能力已经迁入 StoryCut，包括配音安全变速、英文配音 Whisper 字幕兜底、字幕高级样式和导出诊断文件。

```text
storycut_v2/    StoryCut 应用代码（内部目录名为兼容旧项目路径而保留）
models/         共用 Faster-Whisper 模型，不提交
venv/           Python 虚拟环境，不提交
.env            API 地址与密钥，不提交
export/         用户可见的统一导出目录，不提交
requirements.txt
```

## 安装

在仓库根目录创建虚拟环境并一次安装全部依赖：

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

还需要安装 FFmpeg 和 ffprobe，可加入系统 PATH，也可在应用配置中指定绝对路径。Faster-Whisper 模型位置和自动下载方式参见 [MODEL_DOWNLOAD.md](MODEL_DOWNLOAD.md)。

## 启动

双击根目录中的：

```text
点我启动StoryCut（AI解说剪辑）.vbs
```

启动器使用 `pythonw.exe`，正常运行时不会显示 CMD 窗口。也可在根目录运行：

```powershell
venv\Scripts\python.exe storycut_v2/main.py
```

详细功能与使用说明见 [storycut_v2/README.md](storycut_v2/README.md)。

## 本地数据

以下内容受到 Git 忽略和内置更新器保护，不会提交或被版本更新覆盖：

- `.env`
- `models/`
- `venv/`
- `storycut_v2/config.user.yaml`
- `storycut_v2/projects/`
- `storycut_v2/cache/`
- `export/`
- 旧 `storycut_v1/config.user.yaml`、`storycut_v1/output/` 和 `storycut_v1/cache/`

旧 V1 程序文件会随 `2.0.0` 更新移除，但本机遗留的用户配置和历史输出会保留。它们不再被当前 StoryCut 使用，可由用户确认无用后自行归档或删除。

## 更新方式

StoryCut 启动后会静默检查 GitHub 版本。有新版时，更新按钮显示可更新标识；用户确认后：

1. 正常 Git 克隆且位于 `main` 分支、受跟踪程序文件无本地修改时，后台执行快进更新。
2. 没有 Git、不是克隆目录或 Git 更新不可用时，自动使用内置 ZIP 清单同步。

两种方式都不会执行 `git clean`、强制重置或删除未知文件。ZIP 更新会先备份将要变更的程序文件到 `.update_backups/`，并严格保护上述本地数据。

## 当前版本

- 仓库版本：`2.0.0`
- StoryCut 版本：`0.2.0`
