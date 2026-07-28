# SubDub Forge

这是一个包含两套独立视频二创工具的 Windows 仓库：

```text
translator_studio/      中文逐句翻译与配音合成工具
commentary_studio/      AI 解说剪辑工具 StoryCut Studio
models/                  两套工具共用的本地模型
venv/                    两套工具共用的 Python 虚拟环境
.env                     两套工具共用的 API 配置
requirements.txt         共用基础依赖
```

## 两套工具

### Translator Studio

旧的一比一/压缩翻译工具，当前版本 `v1.0.2`。

- 启动：双击 `点我启动翻译工具.vbs`
- 代码与配置：[translator_studio/README.md](translator_studio/README.md)
- 本地输出：`translator_studio/output/`

### StoryCut Studio

AI 解说剪辑工具，当前版本 `v0.1.4`，已经完成初版封版。

- 启动：双击 `点我启动StoryCut.vbs`
- 代码与配置：[commentary_studio/README.md](commentary_studio/README.md)
- 本地项目：`commentary_studio/projects/`

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
- `translator_studio/config.user.yaml`
- `translator_studio/output/`
- `commentary_studio/config.user.yaml`
- `commentary_studio/projects/`

两个应用拥有各自的默认配置和用户配置，只有 API 密钥、模型、虚拟环境和 FFmpeg 等大型依赖共享。

两个应用启动约 1 秒后会在后台检查更新；有新版本时，原“检查更新”按钮会显示可更新标识，不会自动弹窗。两个 `.vbs` 均使用 `pythonw.exe`，启动和运行期间不会显示 CMD；依赖缺失时会直接弹窗说明。

根目录的 `version.json` 和 `update_manifest.json` 仅用于兼容旧版 Translator 更新器；
Translator 当前实际使用的版本与更新清单位于 `translator_studio/`。
