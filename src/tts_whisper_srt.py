from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

from pipeline import ROOT, load_yaml_config, transcribe
from config_manager import load_config as load_project_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe processed English TTS audio to a synchronized SRT")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default="")
    args = parser.parse_args()

    cfg = deepcopy(load_yaml_config(Path(args.config).resolve()) if args.config else load_project_config())
    cfg.setdefault("asr", {})["language"] = "en"
    input_audio = Path(args.input).resolve()
    output_srt = Path(args.output).resolve()
    if not input_audio.exists():
        raise FileNotFoundError(f"English TTS audio not found: {input_audio}")
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    transcribe(cfg, input_audio, output_srt)


if __name__ == "__main__":
    main()
