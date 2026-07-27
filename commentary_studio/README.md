# StoryCut Studio v0.1.0

StoryCut Studio 是独立的 AI 解说剪辑工具，用于把较长的中文原片整理成适合 YouTube Shorts 等平台发布的精简英文解说视频。

初版已经完成“理解原片 → 组织故事 → 匹配镜头 → 预览导出”的完整工作流。旧的一比一翻译工具保持原样，两套工具共用仓库根目录的 `.env`、`models/` 和虚拟环境。

## 初版功能

### 1. 理解原片

- 使用 FFmpeg/ffprobe 读取视频信息并生成封面。
- 使用本地 Faster-Whisper 转写中文语音。
- 检测场景、抽取关键帧并整理事件列表。
- 可选调用 OpenAI 兼容视觉接口描述关键帧。
- 后台显示分析进度、预计总用时和动态剩余时间。

### 2. 组织故事

- 调用 OpenAI 兼容接口生成精简英文解说。
- 支持 60、90、120、180 秒目标上限。
- 英文文案可在界面内编辑并自动保存。
- 可生成 GPT-SoVITS 使用的文本和英文参考 SRT。
- 参考 SRT 按英文句号、问号、感叹号拆句。

### 3. 匹配镜头

- 根据事件绑定、字幕和视觉描述匹配原片镜头。
- 每句解说提供多个候选镜头。
- 支持替换镜头及以 0.5 秒为单位调整入点、出点。
- 镜头不足时可自动组合多个场景。
- 时间线保存到项目目录，关闭后可继续。

### 4. 预览导出

- 导入 GPT-SoVITS 生成的英文音频和同步 SRT。
- 最终视频完全移除原片声音，只保留英文配音。
- 默认保持原视频分辨率和宽高比，不缩放、不补边、不裁切。
- 使用 ASS 烧录英文字幕。
- 提供黑色遮罩、局部模糊和 Delogo 三种字幕底板类型。
- 字幕底板同时遮住原字幕并承载英文字幕，不叠加第二层文字背景。
- 支持字体、字号、边距、描边、粗体及底板区域和效果强度调整。
- 预览区支持方向锁定拖动、中心/边缘吸附、滑块和精确数值输入。
- 可按需调用 FFmpeg 生成真实字幕效果预览。

## 安装

项目使用仓库根目录的共享虚拟环境：

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m pip install -r commentary_studio/requirements.txt
```

必须安装 FFmpeg 和 ffprobe，并加入系统 PATH；也可以在 `config.user.yaml` 中填写绝对路径。

## 启动

双击仓库根目录：

```text
启动解说剪辑台.bat
```

也可以运行：

```powershell
venv\Scripts\python.exe commentary_studio/main.py
```

## 配置与本地数据

- `config.default.yaml`：默认配置，提交到 Git。
- `config.user.yaml`：本机覆盖配置，不提交。
- `projects/`：用户项目、分析产物、缓存和导出视频，不提交。
- `../.env`：与旧工具共用的 API 密钥，不提交。
- `../models/`：与旧工具共用的本地模型，不提交。

每个 StoryCut 项目包含：

```text
analysis/    转写、场景、关键帧和事件
script/      故事稿及 GPT-SoVITS 文案
timeline/    镜头匹配和粗剪时间线
audio/       英文配音和同步字幕
cache/       封面及预览缓存
exports/     导出视频
project.json 项目状态与字幕设置
```

## 初版边界

- GPT-SoVITS 暂不内嵌，继续采用导出 SRT/文案、外部生成、再导入的流程。
- Delogo 是基于周边像素插值的传统 FFmpeg 修复，复杂动态背景下可能出现拉伸纹理。
- 当前以单项目人工确认流程为主，尚未支持批量任务。
- 竖屏画布、自动裁切和主体跟踪不属于默认行为，留待后续作为可选功能。

## 验证状态

初版已使用真实短视频完成全流程验证，包括原片分析、故事生成、镜头匹配、GPT-SoVITS 音频与 SRT 导入、字幕样式设置、真实效果预览和最终视频导出。
