# 声画译匠 · SubDub Forge

当前版本：`v1.0.4`

这是一个面向 Windows 的视频翻译与二创辅助工具。它可以识别视频中的中文语音、翻译并压缩为英文字幕、接收外部 TTS 生成的英文配音和同步字幕，最后完成原字幕擦除、英文字幕烧录、音画节奏适配和成片导出。

项目以图形界面为唯一推荐入口，无需依次运行多个脚本。

本工具位于仓库的 `storycut_v1/`，与 `storycut_v2/` 相互独立；两者只共享
仓库根目录的虚拟环境、`.env`、`models/` 和 FFmpeg。

## 主要功能

- 选择或更换输入视频，并使用文件指纹防止误用旧项目产物。
- 使用 Faster-Whisper 从原视频识别逐段同步的中文字幕。
- 通过 OpenAI 或兼容 API 将中文字幕翻译、压缩为适合配音的英文字幕。
- 在图形界面中保存翻译 API Key、接口地址和模型名称。
- 导入 GPT-SoVITS、Srt-AI-Voice-Assistant 等 TTS 工具生成的英文音频。
- 让 TTS 音频和 TTS 同步 SRT 按相同倍速整体加速或减速。
- 未提供 TTS 同步 SRT 时，可选择 Faster-Whisper 重新识别英文音频生成字幕。
- 支持“单句音频自适应变速”和“视频逐段变速适配英文音频”两种合成方式。
- 支持 Delogo、局部柔化模糊、遮罩三种原字幕擦除方式。
- 提供字幕样式实时预览、字体预览、颜色预览、拖动定位和精确数值输入。
- 支持淡入淡出、弹出等字幕动画，以及边框、阴影、粗体、斜体、字间距等样式。
- 合成前检查文件完整性，输出 FFmpeg 日志和视频局部变速报告。
- 用户需要的文件保留在 `output` 根目录，程序内部文件集中在 `output/_internal`。
- 主界面可直接检查并安装 GitHub 稳定版更新，更新前自动备份程序文件。

## 推荐工作流程

```text
选择视频
  → 1 识别中文字幕
  → 2 翻译英文字幕
  → 外部 TTS 生成英文音频和同步 SRT
  → 3 导入并处理 TTS 音频
  → 设置字幕样式和音频拷入方式
  → 4 合成最终视频
```

## 一、安装与准备

### 1. Windows 和 Python

建议使用 Python 3.10 或更高版本。安装 Python 时勾选“Add Python to PATH”。

在仓库根目录打开 PowerShell，安装共享依赖：

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
```

主要 Python 依赖：

- `PyYAML`：读取和保存项目配置；
- `faster-whisper`：中英文语音识别；
- `openai`：调用 OpenAI 兼容翻译接口；
- `charset-normalizer`：辅助处理文本编码。

### 2. FFmpeg

必须安装 `ffmpeg` 和 `ffprobe`，并将其加入系统 PATH。

可以使用 Windows 包管理器安装：

```powershell
winget install Gyan.FFmpeg
```

安装后重新打开终端并检查：

```powershell
ffmpeg -version
ffprobe -version
```

如果不希望加入 PATH，也可以在本机 `config.user.yaml` 中填写绝对路径：

```yaml
ffmpeg:
  ffmpeg_bin: D:/tools/ffmpeg/bin/ffmpeg.exe
  ffprobe_bin: D:/tools/ffmpeg/bin/ffprobe.exe
```

### 3. Faster-Whisper 模型

当前默认模型是 `large-v3`，模型目录为：

```text
../models/faster-whisper/
```

仓库的 `.gitignore` 默认不上传 `models/`，因此从 GitHub 克隆项目后，需要自行准备模型。详细目录结构和离线使用方法见 `../MODEL_DOWNLOAD.md`。

当前默认配置使用：

```yaml
asr:
  model: large-v3
  model_dir: ../models/faster-whisper
  local_files_only: true
  device: auto
  compute_type: float16
  cpu_compute_type: int8
```

`device: auto` 的行为：

- 检测到可用 NVIDIA 显卡和驱动时，使用 CUDA + float16；
- 没有 NVIDIA 显卡或 CUDA 不可用时，使用 CPU + int8；
- AMD 显卡不能使用本项目当前的 CUDA 后端，会自动转为 CPU；
- CPU 运行 `large-v3` 较慢，日志会定时显示模型加载和识别进度。

## 二、启动图形界面

双击项目根目录中的：

```text
点我启动StoryCut V1（一比一翻译）.vbs
```

该入口使用 `pythonw.exe`，启动和运行期间不会显示 CMD；依赖缺失时会直接弹窗说明。

如果启动器提示找不到项目虚拟环境，请先在仓库根目录创建 `venv` 并安装依赖。

## 三、选择和更换视频

主界面顶部显示当前项目视频。首次使用时点击“选择 / 更换视频”。

选择视频后，程序会把绝对路径和文件指纹写入本机 `config.user.yaml`。项目检查会使用指纹判断视频是否被替换或修改。

如果更换视频时 `output` 中已有字幕、配音或成片，程序会先询问是否继续。确认后，现有用户文件会备份到：

```text
output/_internal/project_backups/日期_时间/
```

这样可以避免新视频误用旧视频的字幕、音频和成片。

## 四、第一步：识别中文字幕

点击：

```text
1 识别中文字幕
```

程序会：

1. 从输入视频提取 16kHz 单声道音频；
2. 保存内部音频为 `output/_internal/audio.wav`；
3. 使用 Faster-Whisper 分析真实语音时间；
4. 输出逐段同步的中文字幕 `output/zh.srt`。

这里的“逐段同步”表示每个 Whisper 语音片段都有实际开始和结束时间，并非简单按视频总时长平均分配。它不是逐字级强制对齐，但适合字幕和后续翻译。

建议在第二步前打开 `zh.srt` 检查：

- 是否有明显漏识别或错字；
- 断句是否合理；
- 开头和结尾时间是否覆盖完整；
- 视频中的背景音乐是否影响语音识别。

## 五、第二步：翻译英文字幕

### 1. 设置翻译 API

点击主界面的：

```text
翻译 API 设置
```

可以填写：

- API Key；
- OpenAI 官方或第三方兼容接口地址；
- 模型名称，默认 `gpt-4o-mini`。

API Key 输入框默认隐藏。保存后：

- API Key 写入本机 `.env`；
- 接口地址和模型保存到本机 `config.user.yaml`；
- API Key 不会写入运行日志；
- `.env` 已加入 `.gitignore`，不会被 Git 上传；
- GitHub 仓库只应保留 `.env.example`。

官方 OpenAI 接口可以将“接口地址”留空。第三方 OpenAI 兼容接口一般需要填写完整的 `/v1` 地址，并使用该平台支持的模型名称。

### 2. 执行翻译

点击：

```text
2 翻译英文字幕
```

程序按批次翻译 `zh.srt`，并强制保留原字幕的编号和时间戳。默认提示词会主动压缩英文表达，减少英文 TTS 比中文音频长太多的问题。

生成文件：

```text
output/en_raw.srt
```

`en_raw.srt` 的文本已经翻译为英文，但时间轴仍与原 `zh.srt` 相同，尚不代表 TTS 英文音频的真实节奏。

如果某批 API 返回的字幕数量不正确，程序会自动重试并暂时生成：

```text
en_raw.batch_*.try_*.debug.txt
```

整个英文字幕成功生成后，这些调试文件会自动删除；只有最终失败时才保留用于排错。

## 六、使用外部 TTS 生成英文语音

将下面的文件交给 GPT-SoVITS、Srt-AI-Voice-Assistant 或其他 TTS 工具：

```text
output/en_raw.srt
```

推荐让 TTS 工具同时导出：

1. 英文音频，例如 WAV、MP3、FLAC、M4A、AAC 或 OGG；
2. 与该音频真实节奏对应的同步英文 SRT。

TTS 同步 SRT 很重要：它记录了每句话在 TTS 音频中实际开始和结束的时间。它与 `en_raw.srt` 的原视频时间轴不是同一个概念。

## 七、第三步：导入并处理 TTS 音频

点击：

```text
3 导入并处理 TTS 音频
```

主界面会先让用户选择 TTS 生成的英文音频，然后打开“TTS 音频修改”窗口。

### 1. 整体音频变速

当前允许范围：

```text
0.90x ～ 1.25x
```

- 大于 `1.00x`：加速；
- 小于 `1.00x`：减速；
- 界面实时显示原始时长和变速后预计时长；
- 如果 `1.25x` 后仍明显长于原视频，会提示进一步压缩英文翻译。

这一步属于 TTS 音频预处理，目的是先缩小英文音频与原视频的总时长差距，不负责单独调整每句话。

### 2. 同步字幕来源

必须选择以下一种方式，默认不启用 Whisper：

#### 推荐：选择 TTS 导出的同步英文 SRT

音频以某个倍速变化时，程序会让 SRT 的所有开始和结束时间使用相同比例变化。例如音频加速 `1.25x`，字幕时间戳全部除以 `1.25`。

这样仍然保留 TTS 原本的逐句同步关系，也能保留原英文文字和断句。

#### 备用：Faster-Whisper 重新识别英文音频

没有 TTS SRT 时，可以勾选 Whisper。程序会先完成音频变速，再以英文模式识别变速后的音频。

该方式可能耗时较长，识别文字和断句也可能与 `en_raw.srt` 不完全一致，因此默认不选中。

如果既没有选择 TTS SRT，也没有勾选 Whisper，程序会阻止导出并说明处理方法，不再用 `en_raw.srt` 按总时长粗略拉伸。

### 3. 第三步输出

成功后同时生成：

```text
output/en_voice.wav
output/en_synced.srt
```

音频和字幕会先写入内部临时文件，只有两者都成功后才一起替换正式文件，避免出现“新音频配旧字幕”的半完成状态。

## 八、音频拷入设置

点击：

```text
音频拷入设置
```

### 模式一：单句音频自适应变速

- 保持原视频速度和总时长；
- 按中文字幕的单句时间窗调整英文语音；
- 适合大多数普通视频；
- 极端长句可能被明显加速。

### 模式二：视频逐段变速适配英文音频

- 保持英文配音原速；
- 以 `en_synced.srt` 的英文音频时间轴为准；
- 按句子和间隙调整视频局部速度；
- 默认限制局部速度为 `0.75x ～ 1.35x`；
- 小于 `0.60秒` 的短区间会优先合并，减少速度跳变；
- 超过上限时可能使用轻微画面跳转；
- 低于下限时可能使用短暂末帧停留；
- 英文音频和最终总时长保持完整。

异常区间报告保存在：

```text
output/_internal/video_segment_fit_report.txt
```

## 九、字幕位置与样式设置

点击：

```text
字幕位置与样式设置
```

窗口内置视频画面实时预览，不会先保存预览图片。预览中始终显示示例中英文字幕。

### 原字幕擦除

支持：

- `Delogo`：默认选项，适合位置固定的硬字幕；
- 局部柔化模糊：对字幕区域持续模糊；
- 遮罩：使用指定透明度的色块覆盖，效果最直接。

擦除区域在整段视频中持续生效，避免字幕间隙露出原字幕。拖动预览区域时只调整位置，不改变宽高；背景区域和示例文字一起移动。

### 英文字幕样式

可以调整：

- 本机已安装字体及字体预览；
- 字号、位置、对齐和底部边距；
- 文字颜色、边框颜色及 RGBA 色块预览；
- 边框、淡阴影、粗体、斜体和字间距；
- 无动画、淡入淡出、弹出等动画。

颜色在界面中使用 `#RRGGBBAA`，例如：

```text
#FFFFFFFF  不透明白色
#00000090  半透明黑色
```

所有滑块旁都有精确数值输入框，适合微调。样式需要点击保存后才写入 `config.user.yaml`。

## 十、合成最终视频

点击：

```text
4 合成最终视频
```

合成前必须存在：

```text
output/en_raw.srt
output/en_voice.wav
output/en_synced.srt
```

缺少文件时，图形界面会列出具体缺失项和生成方法。

默认合成行为：

1. 使用选定方式持续擦除原视频字幕区域；
2. 将英文字幕和动画烧录进画面；
3. 使用英文配音替换原视频音频；
4. 根据所选音频拷入模式调整音频或视频节奏；
5. 输出 H.264 视频和 AAC 音频。

最终文件：

```text
output/final.mp4
```

完整 FFmpeg 日志：

```text
output/_internal/compose_ffmpeg.log
```

## 十一、一键智能运行与项目检查

### 一键智能运行/继续

程序会根据现有文件决定下一步：

- 没有 `zh.srt`：执行中文字幕识别；
- 有 `zh.srt` 但没有 `en_raw.srt`：执行翻译；
- 缺少 TTS 音频或同步字幕：暂停并提示完成第三步；
- 所有文件齐备：执行最终合成。

它不会自动调用外部 GPT-SoVITS，因此 TTS 阶段仍需用户操作。

### 检查项目

用于检查：

- 当前输入视频是否存在；
- 视频文件指纹是否变化；
- 英文字幕是否缺少对应中文字幕；
- `en_voice.wav` 和 `en_synced.srt` 是否成对存在；
- `final.mp4` 是否可能早于当前输入视频；
- 最终合成所需文件是否齐备。

## 十二、输出目录说明

用户直接使用的文件位于：

```text
output/
  zh.srt             中文识别字幕
  en_raw.srt         英文翻译字幕，保留原视频时间轴
  en_voice.wav       处理后的英文配音
  en_synced.srt      与英文配音逐段同步的字幕
  final.mp4          最终成片
```

程序内部文件位于：

```text
output/_internal/
  audio.wav
  animated_subtitles.ass
  compose_filters.txt
  compose_ffmpeg.log
  video_segment_fit.mp4
  video_segment_fit_filters.txt
  video_segment_fit_report.txt
  project_backups/
  ...
```

内部目录可以用于排错，但不建议在任务运行时手动修改。程序会按时间戳和版本信息决定是否复用部分中间文件。

## 十三、项目主要文件

```text
video_translate_auto/
  点我启动StoryCut V1（一比一翻译）.vbs       V1 无 CMD 启动入口
  点我启动StoryCut V2（AI解说剪辑）.vbs       V2 无 CMD 启动入口
  LICENSE                       MIT 开源许可证
  requirements.txt              Python 依赖
  MODEL_DOWNLOAD.md             Whisper 模型与离线加载说明
  .env.example                  API 环境变量示例
  .gitignore                    Git 忽略规则
  models/                       两个应用共用模型
  .env                          两个应用共用 API 配置
  storycut_v1/
    config.default.yaml         旧工具默认配置
    config.user.yaml            旧工具本机配置
    version.json                旧工具版本
    update_manifest.json        旧工具更新清单
    README.md                   本说明
    output/                     旧工具用户输出
    src/
      gui_launcher.py           主图形界面
      pipeline.py               识别、翻译、适配和合成主流程
      style_editor.py           字幕擦除、位置和样式设置
      update_manager.py         仓库级更新器入口
  storycut_v2/            StoryCut Studio
```

## 十四、应用更新

点击主界面的“检查应用更新”按钮，程序会读取 GitHub `main` 分支上的版本信息：

- 没有新版时，提示当前已是最新版；
- 有新版时，显示版本号和更新说明；确认后优先在后台执行 `git pull --ff-only origin main`，无法使用 Git 时自动下载 GitHub `main` ZIP；
- 安装完成后提示重启，选择“是”会重新启动图形界面；
- 更新前会把可能改变的旧程序文件备份到根目录 `.update_backups/`；
- 如果安装中途失败，程序会尽力自动恢复旧文件。

更新器依据根 `update_manifest.json` 同步 StoryCut V1、V2、根目录启动器和文档，同时删除清单中已废弃的旧程序文件。共享的 `.env`、`models/`、`venv/`、两套用户配置、项目、`output/` 和 `export/` 均不会被修改或删除。

程序启动约 1 秒后也会在后台静默检查一次；发现新版时，原更新按钮会变为“↑ 可更新 v版本号”，不会自动弹窗。

## 十五、GitHub 发布注意事项

配置采用分层合并：程序先读取 `config.default.yaml`，再用 `config.user.yaml` 中的用户值递归覆盖。更新程序可以安全更新默认配置，而不会覆盖视频路径、字幕样式、字体收藏等本机设置。

以下内容不应上传：

- `../.env`：包含两套工具共用的真实 API Key；
- `config.user.yaml`：包含本机路径和个人设置；
- `../models/`：模型文件体积很大；
- `output/`：包含用户视频、音频、字幕和成片；
- `venv/`：本机 Python 虚拟环境；
- `.agents/`、`.joycode/`：本地开发工具元数据；
- `__pycache__/` 和 `*.pyc`：Python 缓存。

项目已经通过 `.gitignore` 忽略这些内容。仓库中应保留 `.env.example`，让用户知道需要配置哪些变量。

如果 API Key、Hugging Face Token 或其他密钥曾经提交到 GitHub，仅删除文件是不够的，必须前往对应平台撤销并重新生成密钥。

## 十五、常见问题

### 1. `ffmpeg` 或 `ffprobe` 找不到

将 FFmpeg 加入 PATH，重启图形界面；或者在 `config.user.yaml` 中填写可执行文件绝对路径。

### 2. 找不到 Faster-Whisper 模型

当前默认 `local_files_only: true`，程序不会自动下载。请按照 `MODEL_DOWNLOAD.md` 将完整模型放到正确目录，或者在本机 `config.user.yaml` 中覆盖为 `false` 后允许程序下载。

### 3. CUDA driver version is insufficient

可能是 NVIDIA 驱动过旧，也可能电脑根本没有 NVIDIA 显卡。程序使用 `device: auto` 时会自动转为 CPU。

AMD 显卡不能通过更新 AMD 驱动获得 CUDA 支持。若使用 NVIDIA RTX 显卡，安装适合该显卡的最新 NVIDIA 驱动后通常可以恢复 CUDA 加速。

### 4. CPU 识别长时间没有完成

`large-v3` 在 CPU 上较慢。日志会显示模型加载心跳、已等待时间、已识别片段数量和当前音频位置。只要持续输出心跳，通常仍在运行。

### 5. 翻译提示没有 API Key

点击“翻译 API 设置”，输入 Key 并保存。不要把真实 `.env` 上传到 GitHub。

### 6. 翻译生成 debug 文件

说明某一批 API 返回的 SRT 块数量不正确。程序会自动重试；最终成功后自动删除，最终失败时保留用于排错。

### 7. 第三步不允许导出

必须选择 TTS 同步 SRT，或者主动勾选 Whisper 生成字幕。推荐使用与音频同时由 TTS 导出的 SRT。

### 8. 英文音频明显比原视频长

先在第三步使用最高 `1.25x` 整体加速。如果仍明显过长，应修改翻译提示词或英文文案，进一步压缩表达，而不是无限提高语速。

### 9. 视频局部变速很突兀

在“音频拷入设置”中调整允许的局部速度范围和短区间合并阈值，也可以改用“单句音频自适应变速”。具体异常段可查看 `video_segment_fit_report.txt`。

### 10. 原字幕偶尔露出

擦除滤镜默认持续作用于整段视频。如果仍能看到原字幕，应在样式设置中扩大擦除区域，或在 Delogo、柔化模糊和遮罩之间切换。

### 11. 最终合成出现内存不足

查看 `output/_internal/compose_ffmpeg.log` 最后部分。当前版本已将复杂滤镜写入脚本文件，并让字幕擦除持续生效，避免为大量字幕生成超长 `between()` 表达式。

## 十六、当前限制

- 外部 TTS 仍需在 GPT-SoVITS、Srt-AI-Voice-Assistant 等工具中完成；
- 默认使用英文配音完全替换原音轨，尚未自动分离并保留背景音乐；
- AMD GPU 暂不支持当前 Faster-Whisper CUDA 加速路径；
- Whisper 兜底生成的英文字幕可能改变原文和断句；
- 视频局部变速受到速度限制时，极端区间可能出现短暂停格或轻微画面跳转；
- 字幕擦除是传统视频滤镜，不是 AI 内容修复，复杂背景下可能留下痕迹。

在正式发布版本前，建议至少使用一个新视频完整测试两种音频拷入模式、两种同步字幕来源以及视频更换/备份流程。
