# Faster-Whisper 模型与离线加载说明

本项目默认使用 Faster-Whisper `large-v3` 识别中文语音，也可在第三步选择用同一套识别引擎生成英文同步字幕。

## 当前默认配置

```yaml
asr:
  model: large-v3
  model_dir: ./models/faster-whisper
  local_files_only: true
  device: auto
  compute_type: float16
  fallback_to_cpu: true
  cpu_compute_type: int8
  cpu_model: large-v3
```

- `local_files_only: true`：只使用本地模型，不访问 Hugging Face，也不会自动下载；
- `device: auto`：检测到可用 NVIDIA GPU 时使用 CUDA，否则使用 CPU；
- `compute_type: float16`：CUDA 模式使用半精度；
- `cpu_compute_type: int8`：CPU 模式使用 int8，减少内存并提高速度；
- `cpu_model`：GPU 不可用时使用的模型，当前仍使用已经下载好的 `large-v3`。

## 模型目录

默认缓存根目录：

```text
models/faster-whisper/
```

程序支持以下几种完整模型布局。

### 直接放在模型根目录

```text
models/faster-whisper/
  model.bin
  config.json
  tokenizer.json
  vocabulary.json
  ...
```

### 按模型名称建立子目录

```text
models/faster-whisper/large-v3/
  model.bin
  config.json
  tokenizer.json
  ...
```

或：

```text
models/faster-whisper/faster-whisper-large-v3/
  model.bin
  config.json
  tokenizer.json
  ...
```

### Hugging Face 标准缓存布局

```text
models/faster-whisper/
  models--Systran--faster-whisper-large-v3/
    snapshots/
      版本哈希/
        model.bin
        config.json
        tokenizer.json
        ...
```

程序会直接寻找包含 `model.bin` 和 `config.json` 的最新完整 snapshot，不需要联网查询版本。

## 判断是否使用本地模型

识别日志会显示：

```text
ASR runtime: device=cpu, compute_type=int8, model=D:\...\snapshots\版本哈希
```

`model=` 后是完整本地路径，表示已经找到并直接使用本地模型。

如果只显示：

```text
model=small
```

表示没有解析到本地路径；当 `local_files_only: false` 时，Faster-Whisper 可能尝试从 Hugging Face 下载该模型。

## 手动获取模型

官方转换模型仓库：

```text
https://huggingface.co/Systran/faster-whisper-large-v3
```

必须下载完整模型，至少应包含：

```text
model.bin
config.json
tokenizer.json
preprocessor_config.json
vocabulary.json 或 vocabulary.txt
```

只下载 `config.json` 或留下 `.incomplete` 文件不能运行。

## 允许自动下载

如果确实希望程序联网下载，可以在本机 `config.user.yaml` 中覆盖：

```yaml
asr:
  local_files_only: false
```

首次使用时会从 Hugging Face 下载，文件可能较大。下载完成并确认可以运行后，可以重新改回 `true`。

不要将 Hugging Face Token 写入项目普通文本文件。需要 Token 时应使用本机环境变量，并确保相关文件已被 `.gitignore` 忽略。

## 常见模型

```text
large-v3       Systran/faster-whisper-large-v3       精度较高，体积大，CPU 较慢
large-v3-turbo Systran/faster-whisper-large-v3-turbo 速度更快
medium         Systran/faster-whisper-medium          精度和速度折中
small          Systran/faster-whisper-small           CPU 更轻量，需要单独下载
```

项目不会从 `large-v3` 自动生成 `small` 模型。配置为哪个模型，就必须存在该模型的完整文件，或者允许联网下载。

## GPU 与驱动

当前 Faster-Whisper/CTranslate2 的 GPU 路径使用 NVIDIA CUDA：

- NVIDIA RTX 显卡可以在驱动和运行库兼容时使用 CUDA；
- 驱动过旧可能出现 `CUDA driver version is insufficient for CUDA runtime version`；
- AMD Radeon 显卡不支持 CUDA，更新 AMD 驱动也不能变成 CUDA 设备；
- 无可用 NVIDIA GPU 时，程序自动使用 CPU + int8。

模型本身既可以在 GPU 上运行，也可以在 CPU 上运行；区别在于速度、计算精度和所需运行库，不需要为 CPU 重新下载同名模型。
