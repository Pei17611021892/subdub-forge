from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import wave
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config_manager import load_config, save_config


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
INTERNAL = OUTPUT / "_internal"
VOICE = OUTPUT / "en_voice.wav"
SOURCE_SRT = OUTPUT / "en_raw.srt"
SYNCED_SRT = OUTPUT / "en_synced.srt"
ZH_SRT = OUTPUT / "zh.srt"
HARD_MIN_SPEED = 0.90
HARD_MAX_SPEED = 1.25
WHISPER_HELPER = ROOT / "src" / "tts_whisper_srt.py"


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / wav.getframerate()


def format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minute, sec = divmod(seconds, 60)
    hour, minute = divmod(int(minute), 60)
    return f"{hour:02d}:{minute:02d}:{sec:05.2f}"


def stamp_seconds(value: str) -> float:
    h, minute, rest = value.replace(",", ".").split(":")
    return int(h) * 3600 + int(minute) * 60 + float(rest)


def srt_stamp(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    h, millis = divmod(millis, 3_600_000)
    minute, millis = divmod(millis, 60_000)
    sec, ms = divmod(millis, 1000)
    return f"{h:02d}:{minute:02d}:{sec:02d},{ms:03d}"


class TtsAudioEditor(tk.Tk):
    def __init__(self, input_audio: Path | None = None) -> None:
        super().__init__()
        self.title("TTS 音频修改（变速/导出 SRT）")
        self.geometry("800x700")
        self.minsize(740, 640)
        self.min_speed = tk.DoubleVar(value=HARD_MIN_SPEED)
        self.max_speed = tk.DoubleVar(value=HARD_MAX_SPEED)
        self.speed = tk.DoubleVar(value=1.00)
        self.duration = 0.0
        self.target_duration = 0.0
        self.input_audio = input_audio or VOICE
        self.input_srt: Path | None = None
        self.use_whisper = tk.BooleanVar(value=False)
        self.exporting = False
        self._build()
        self._load_config()
        self.refresh_audio()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, text="TTS 音频修改（变速/导出 SRT）", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor=tk.W)
        self.input_label = ttk.Label(root, text=f"选择的音频：{self.input_audio}", foreground="#555", wraplength=700)
        self.input_label.pack(anchor=tk.W, pady=(4, 14))

        speed_box = ttk.LabelFrame(root, text="1. TTS 音频整体预处理（保持音调）", padding=12)
        speed_box.pack(fill=tk.X)
        limits = ttk.Frame(speed_box)
        limits.pack(fill=tk.X)
        ttk.Label(limits, text="速度下限").pack(side=tk.LEFT)
        ttk.Spinbox(limits, from_=HARD_MIN_SPEED, to=HARD_MAX_SPEED, increment=0.05, format="%.2f", width=7, textvariable=self.min_speed, command=self._limits_changed).pack(side=tk.LEFT, padx=(6, 18))
        ttk.Label(limits, text="速度上限").pack(side=tk.LEFT)
        ttk.Spinbox(limits, from_=HARD_MIN_SPEED, to=HARD_MAX_SPEED, increment=0.05, format="%.2f", width=7, textvariable=self.max_speed, command=self._limits_changed).pack(side=tk.LEFT, padx=(6, 18))
        ttk.Label(limits, text="硬限制 0.90x–1.25x", foreground="#666").pack(side=tk.LEFT)

        choose = ttk.Frame(speed_box)
        choose.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(choose, text="本次速度", width=10).pack(side=tk.LEFT)
        self.speed_scale = ttk.Scale(choose, from_=HARD_MIN_SPEED, to=HARD_MAX_SPEED, command=self._scale_speed_changed)
        self.speed_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 8))
        speed_spin = ttk.Spinbox(choose, from_=HARD_MIN_SPEED, to=HARD_MAX_SPEED, increment=0.01, format="%.2f", width=8, textvariable=self.speed, command=self._duration_changed)
        speed_spin.pack(side=tk.LEFT)
        speed_spin.bind("<KeyRelease>", lambda _e: self._duration_changed())
        speed_spin.bind("<Return>", self._normalize_speed)
        speed_spin.bind("<FocusOut>", self._normalize_speed)

        self.duration_label = ttk.Label(speed_box, text="", font=("Microsoft YaHei UI", 11, "bold"))
        self.duration_label.pack(anchor=tk.W, pady=(12, 3))
        # ttk.Scale.set() invokes its command immediately on some Tk versions.
        # Set the initial value only after every callback-dependent widget exists.
        self.speed_scale.set(self.speed.get())
        ttk.Label(
            speed_box,
            text="用于先把异常偏长的 TTS 音频压回合理区间；它不负责逐句对齐。速度 > 1 为加速，速度 < 1 为慢放。",
            foreground="#555", wraplength=700,
        ).pack(anchor=tk.W)
        srt_box = ttk.LabelFrame(root, text="2. 同步英文字幕来源（必须选择一种）", padding=12)
        srt_box.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(
            srt_box,
            text="推荐选择 TTS 与该音频一起导出的同步英文 SRT。导出时字幕时间戳会与音频使用完全相同的变速比例，因此仍保持逐句同步。",
            wraplength=680, foreground="#555",
        ).pack(anchor=tk.W)
        choose_srt = ttk.Frame(srt_box)
        choose_srt.pack(fill=tk.X, pady=(9, 0))
        ttk.Button(choose_srt, text="选择 TTS 导出的同步英文 SRT", command=self.choose_tts_srt).pack(side=tk.LEFT)
        self.srt_label = ttk.Label(choose_srt, text="尚未选择", foreground="#b42318", wraplength=430)
        self.srt_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)
        ttk.Checkbutton(
            srt_box,
            text="未提供 TTS SRT 时，使用 Faster-Whisper 识别变速后的英文音频（较慢，断句和文字可能变化）",
            variable=self.use_whisper,
            command=self._subtitle_source_changed,
        ).pack(anchor=tk.W, pady=(12, 0))
        ttk.Label(
            srt_box,
            text="Whisper 默认不启用。上传 TTS SRT 更快、更准确，也能保留原有英文文案和断句。",
            foreground="#8a5a00", wraplength=690,
        ).pack(anchor=tk.W, padx=(22, 0), pady=(4, 0))

        self.export_button = ttk.Button(root, text="导出 en_voice.wav + en_synced.srt", command=self.export_speed)
        self.export_button.pack(anchor=tk.W, pady=(14, 0))

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(16, 0))
        ttk.Button(actions, text="刷新音频信息", command=self.refresh_audio).pack(side=tk.LEFT)
        ttk.Button(actions, text="仅重新生成 en_synced.srt", command=self.export_srt).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(actions, text="关闭", command=self.destroy).pack(side=tk.LEFT, padx=8)
        self.status = ttk.Label(actions, text="")
        self.status.pack(side=tk.RIGHT)

    def _load_config(self) -> None:
        cfg = load_config()
        edit = cfg.get("tts_audio_edit", {})
        self.min_speed.set(float(edit.get("min_speed", HARD_MIN_SPEED)))
        self.max_speed.set(float(edit.get("max_speed", HARD_MAX_SPEED)))
        self.speed.set(float(edit.get("speed", 1.00)))
        self._limits_changed()

    def _save_config(self) -> None:
        cfg = load_config()
        edit = cfg.setdefault("tts_audio_edit", {})
        edit["min_speed"] = round(self.min_speed.get(), 2)
        edit["max_speed"] = round(self.max_speed.get(), 2)
        edit["speed"] = round(self.speed.get(), 2)
        save_config(cfg)

    def _limits_changed(self) -> None:
        low = max(HARD_MIN_SPEED, min(HARD_MAX_SPEED, self.min_speed.get()))
        high = max(HARD_MIN_SPEED, min(HARD_MAX_SPEED, self.max_speed.get()))
        if low > high:
            low, high = high, low
            self.min_speed.set(low)
            self.max_speed.set(high)
        else:
            self.min_speed.set(low)
            self.max_speed.set(high)
        self.speed_scale.configure(from_=low, to=high)
        self.speed.set(round(max(low, min(high, self.speed.get())), 2))
        self.speed_scale.set(self.speed.get())
        self._duration_changed()

    def _scale_speed_changed(self, value: str) -> None:
        rounded = round(float(value), 2)
        if round(self.speed.get(), 2) != rounded:
            self.speed.set(rounded)
        self._duration_changed()

    def _normalize_speed(self, _event=None) -> None:
        try:
            value = round(max(HARD_MIN_SPEED, min(HARD_MAX_SPEED, float(self.speed.get()))), 2)
        except (ValueError, tk.TclError):
            value = 1.00
        self.speed.set(value)
        self.speed_scale.set(value)
        self._duration_changed()

    def _duration_changed(self) -> None:
        if not hasattr(self, "duration_label"):
            return
        try:
            speed = max(0.01, float(self.speed.get()))
            changed = self.duration / speed if self.duration else 0.0
            self.duration_label.configure(
                text=f"原始时长：{format_duration(self.duration)}    变速后预计：{format_duration(changed)}    ({speed:.2f}x)"
            )
            if self.target_duration and changed > self.target_duration + 0.5 and speed >= self.max_speed.get() - 0.005:
                self.duration_label.configure(
                    text=(
                        f"原始时长：{format_duration(self.duration)}    1.25x 后：{format_duration(changed)}\n"
                        f"仍比原视频长 {changed - self.target_duration:.2f} 秒，请修改翻译设置并进一步压缩英文文案。"
                    ),
                    foreground="#b00020",
                )
            else:
                self.duration_label.configure(foreground="")
        except (ValueError, tk.TclError):
            pass

    def refresh_audio(self) -> None:
        self.target_duration = 0.0
        if ZH_SRT.exists():
            try:
                timings = re.findall(r"\d+:\d+:\d+[,.]\d+\s*-->\s*(\d+:\d+:\d+[,.]\d+)", ZH_SRT.read_text(encoding="utf-8-sig"))
                if timings:
                    self.target_duration = stamp_seconds(timings[-1])
            except Exception:
                pass
        if not self.input_audio.exists():
            self.duration = 0.0
            self.status.configure(text="找不到选择的音频", foreground="#b00020")
        else:
            try:
                self.duration = self._media_duration(self.input_audio)
                self.status.configure(text="音频已读取", foreground="#167c2d")
            except Exception as exc:
                self.duration = 0.0
                self.status.configure(text=f"读取失败：{exc}", foreground="#b00020")
        self._duration_changed()

    def _ffmpeg(self) -> str:
        cfg = load_config()
        value = str(cfg.get("ffmpeg", {}).get("ffmpeg_bin", "ffmpeg"))
        return value if Path(value).exists() else (shutil.which(value) or value)

    def _media_duration(self, path: Path) -> float:
        try:
            return wav_duration(path)
        except Exception:
            cfg = load_config()
            probe_name = str(cfg.get("ffmpeg", {}).get("ffprobe_bin", "ffprobe"))
            ffprobe = probe_name if Path(probe_name).exists() else (shutil.which(probe_name) or probe_name)
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr[-800:])
            return float(proc.stdout.strip())

    def choose_tts_srt(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择 TTS 导出的同步英文字幕",
            filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        self.input_srt = Path(selected).resolve()
        self.use_whisper.set(False)
        self.srt_label.configure(text=str(self.input_srt), foreground="#167c2d")

    def _subtitle_source_changed(self) -> None:
        if self.use_whisper.get():
            self.srt_label.configure(text="将使用 Whisper，不使用已选 TTS SRT", foreground="#8a5a00")
        elif self.input_srt:
            self.srt_label.configure(text=str(self.input_srt), foreground="#167c2d")
        else:
            self.srt_label.configure(text="尚未选择", foreground="#b42318")

    def _require_subtitle_source(self) -> bool:
        if self.use_whisper.get() or (self.input_srt and self.input_srt.exists()):
            return True
        messagebox.showwarning(
            "请选择同步英文字幕来源",
            "导出还缺少同步英文字幕来源。请选择以下一种方式：\n\n"
            "推荐：点击“选择 TTS 导出的同步英文 SRT”，选择与当前音频同时导出的字幕。程序会让音频和字幕按相同倍速变化，逐句时间仍然准确。\n\n"
            "备用：勾选“使用 Faster-Whisper”。程序会重新识别变速后的英文音频，但耗时较长，文字和断句可能与 en_raw.srt 不完全相同。\n\n"
            "为避免生成看似同步、实际仅按总时长估算的字幕，现在不再使用 en_raw.srt 自动拉伸。",
        )
        return False

    def export_speed(self) -> None:
        if not self.input_audio.exists():
            messagebox.showerror("无法变速", f"找不到选择的音频：{self.input_audio}")
            return
        if not self._require_subtitle_source():
            return
        self._limits_changed()
        speed = round(max(HARD_MIN_SPEED, min(HARD_MAX_SPEED, float(self.speed.get()))), 2)
        self.speed.set(speed)
        warning = (
            "此次导出会覆盖 output/en_voice.wav 和 output/en_synced.srt。\n\n"
            "如目标文件中已有需要保留的内容，请先自行备份。\n"
            f"当前速度：{speed:.2f}x\n"
            f"预计时长：{format_duration(self.duration / speed)}\n\n"
            + ("字幕方式：Faster-Whisper 重新识别（可能耗时较长）\n\n" if self.use_whisper.get() else f"字幕来源：{self.input_srt}\n\n")
            + "确定继续导出吗？"
        )
        if not messagebox.askokcancel("覆盖音频确认", warning, icon="warning"):
            return
        if self.exporting:
            return
        use_whisper = self.use_whisper.get()
        source_srt = self.input_srt
        self._save_config()
        self.exporting = True
        self.export_button.configure(state=tk.DISABLED)
        self.status.configure(text="正在处理音频和字幕…", foreground="#8a5a00")

        def work() -> None:
            try:
                INTERNAL.mkdir(parents=True, exist_ok=True)
                temp = INTERNAL / "en_voice_speed_export.wav"
                temp_srt = INTERNAL / "en_synced_export.srt"
                cmd = [self._ffmpeg(), "-y", "-i", str(self.input_audio), "-filter:a", f"atempo={speed:.2f}", "-c:a", "pcm_s16le", str(temp)]
                proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr[-1200:])
                if use_whisper:
                    proc = subprocess.run(
                        [sys.executable, str(WHISPER_HELPER), "--input", str(temp), "--output", str(temp_srt)],
                        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                    )
                    if proc.returncode != 0:
                        raise RuntimeError("Whisper 生成字幕失败：\n" + (proc.stdout + proc.stderr)[-1600:])
                else:
                    assert source_srt is not None
                    self._write_synced_srt(source_srt, 1.0 / speed, temp_srt)
                # Replace public outputs only after both operations succeeded, so
                # a subtitle failure cannot leave a new audio paired with an old SRT.
                os.replace(temp, VOICE)
                os.replace(temp_srt, SYNCED_SRT)
                self.after(0, self._export_finished)
            except Exception as exc:
                self.after(0, lambda error=str(exc): self._export_failed(error))

        threading.Thread(target=work, daemon=True).start()

    def _export_finished(self) -> None:
        self.exporting = False
        self.export_button.configure(state=tk.NORMAL)
        self.input_audio = VOICE
        self.input_label.configure(text=f"当前音频：{VOICE}")
        self.refresh_audio()
        self.status.configure(text="音频和逐句同步字幕已导出", foreground="#167c2d")
        messagebox.showinfo("导出完成", f"已生成：{VOICE}\n已生成：{SYNCED_SRT}\n新时长：{format_duration(self.duration)}")

    def _export_failed(self, error: str) -> None:
        self.exporting = False
        self.export_button.configure(state=tk.NORMAL)
        self.status.configure(text="导出失败", foreground="#b00020")
        messagebox.showerror("导出失败", error)

    def _legacy_export_speed_removed(self) -> None:
        """Kept as a marker for older stack traces; export is now asynchronous."""
        return

    def export_srt(self) -> None:
        if not VOICE.exists():
            messagebox.showerror("无法导出", "找不到 output/en_voice.wav")
            return
        if not self._require_subtitle_source():
            return
        try:
            if SYNCED_SRT.exists() and not messagebox.askokcancel("覆盖确认", "output/en_synced.srt 已存在，确定覆盖吗？", icon="warning"):
                return
            if self.use_whisper.get():
                messagebox.showinfo("请使用完整导出", "Whisper 识别可能耗时较长，请点击“导出 en_voice.wav + en_synced.srt”统一处理。")
                return
            assert self.input_srt is not None
            speed = round(max(HARD_MIN_SPEED, min(HARD_MAX_SPEED, float(self.speed.get()))), 2)
            self._write_synced_srt(self.input_srt, 1.0 / speed)
            self.status.configure(text="en_synced.srt 已导出", foreground="#167c2d")
            messagebox.showinfo("导出完成", f"已生成：{SYNCED_SRT}\n结束时间：{format_duration(self.duration)}")
        except Exception as exc:
            messagebox.showerror("SRT 导出失败", str(exc))

    def _write_synced_srt(self, source: Path, time_scale: float, destination: Path = SYNCED_SRT) -> None:
        text = source.read_text(encoding="utf-8-sig")
        matches = list(re.finditer(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", text))
        if not matches:
            raise RuntimeError(f"所选字幕中没有有效时间轴：{source}")
        converted = re.sub(
            r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)",
            lambda m: f"{srt_stamp(stamp_seconds(m.group(1)) * time_scale)} --> {srt_stamp(stamp_seconds(m.group(2)) * time_scale)}",
            text,
        )
        destination.write_text(converted.strip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="")
    args = parser.parse_args()
    selected = Path(args.input).resolve() if args.input else None
    if selected is None:
        chooser = tk.Tk()
        chooser.withdraw()
        from tkinter import filedialog
        picked = filedialog.askopenfilename(title="选择 GPT-SoVITS 生成的音频")
        chooser.destroy()
        selected = Path(picked).resolve() if picked else None
    if selected:
        TtsAudioEditor(selected).mainloop()
