# SubDub Forge

这是一个包含两套独立视频二创工具的 Windows 仓库：

```text
storycut_v1/      中文逐句翻译与配音合成工具
storycut_v2/      AI 解说剪辑工具 StoryCut Studio
models/                  两套工具共用的本地模型
venv/                    两套工具共用的 Python 虚拟环境
.env                     两套工具共用的 API 配置
export/                  两套工具统一的对外导出目录
requirements.txt         共用基础依赖
```

## 两套工具

### StoryCut V1

旧的一比一/压缩翻译工具，当前版本 `v1.0.3`。

- 启动：双击 `点我启动StoryCut V1（一比一翻译）.vbs`
- 代码与配置：[storycut_v1/README.md](storycut_v1/README.md)
- 工作缓存：`storycut_v1/output/`
- 对外导出：根目录 `export/`（后续接入）

### StoryCut V2

AI 解说剪辑工具，当前版本 `v0.1.7`，已经完成初版封版。

- 启动：双击 `点我启动StoryCut V2（AI解说剪辑）.vbs`
- 代码与配置：[storycut_v2/README.md](storycut_v2/README.md)
- 本地项目：`storycut_v2/projects/`
- 对外导出：根目录 `export/`

## 共享依赖

在仓库根目录创建并安装共享虚拟环境：

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

根目录 `requirements.txt` 会一次安装两套工具所需依赖，包括 StoryCut 使用的
PySide6。无需再分别安装第二份依赖文件。

FFmpeg 和 ffprobe 由两套工具共用。可将其加入系统 PATH，或分别在应用的
`config.user.yaml` 中配置绝对路径。

Faster-Whisper 模型统一放在 `models/faster-whisper/`，具体参见
[MODEL_DOWNLOAD.md](MODEL_DOWNLOAD.md)。

## 本地配置

以下文件或目录不会提交 Git：

- `.env`
- `models/`
- `venv/`
- `storycut_v1/config.user.yaml`
- `storycut_v1/output/`
- `storycut_v2/config.user.yaml`
- `storycut_v2/projects/`
- `storycut_v1/cache/`
- `storycut_v2/cache/`
- `export/`

两个应用拥有各自的默认配置和用户配置，只有 API 密钥、模型、虚拟环境和 FFmpeg 等大型依赖共享。

两个应用启动约 1 秒后会在后台检查更新；有新版本时，原“检查更新”按钮会显示可更新标识，不会自动弹窗。确认更新后，程序直接下载 GitHub `main` 分支最新文件，根据根 `update_manifest.json` 同步两个应用、启动器和文档，并删除清单中已废弃的旧程序文件。

更新会先备份受影响的程序文件到 `.update_backups/`；`.env`、`models/`、`venv/`、两套用户配置、项目、`output/` 和 `export/` 均受保护。旧目录版本会通过 `commentary_studio/` 与 `translator_studio/` 中的一次性更新桥接自动迁移，桥接目录不是实际应用。

两个 `.vbs` 均使用 `pythonw.exe`，启动和运行期间不会显示 CMD；依赖缺失时会直接弹窗说明。
