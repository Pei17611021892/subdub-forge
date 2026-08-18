from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from config_manager import load_config, save_config


class AudioFitEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("音频拷入设置")
        self.geometry("760x560")
        self.minsize(720, 520)
        self.mode = tk.StringVar(value="audio_segment_fit")
        self.video_min_speed = tk.DoubleVar(value=0.75)
        self.video_max_speed = tk.DoubleVar(value=1.35)
        self.merge_short_sec = tk.DoubleVar(value=0.60)
        self._build()
        self._load()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, text="音频拷入设置", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(root, text="选择英文配音与原视频节奏不一致时，由音频还是视频进行逐段适配。", foreground="#555").pack(anchor=tk.W, pady=(4, 16))

        audio = ttk.LabelFrame(root, text="原视频优先", padding=12)
        audio.pack(fill=tk.X, pady=(0, 12))
        ttk.Radiobutton(audio, text="单句音频自适应变速", variable=self.mode, value="audio_segment_fit").pack(anchor=tk.W)
        ttk.Label(
            audio,
            text="保持原视频速度，按中文字幕的单句时间窗调整英文语音速度。画面自然，但过长英文句可能语速偏快。",
            wraplength=690, foreground="#555",
        ).pack(anchor=tk.W, padx=(24, 0), pady=(5, 0))

        video = ttk.LabelFrame(root, text="自然语音优先", padding=12)
        video.pack(fill=tk.X)
        ttk.Radiobutton(video, text="视频逐段变速适配英文音频", variable=self.mode, value="video_segment_fit").pack(anchor=tk.W)
        ttk.Label(
            video,
            text="保持英文语音原速，按句子时间轴调整视频。语音自然，但局部画面可能快放、慢放或短暂停格。",
            wraplength=690, foreground="#555",
        ).pack(anchor=tk.W, padx=(24, 0), pady=(5, 0))

        limits = ttk.Frame(video)
        limits.pack(fill=tk.X, padx=(24, 0), pady=(10, 0))
        ttk.Label(limits, text="局部视频速度范围").pack(side=tk.LEFT)
        ttk.Spinbox(limits, from_=0.50, to=1.00, increment=0.05, format="%.2f", width=7, textvariable=self.video_min_speed).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(limits, text="x ～").pack(side=tk.LEFT)
        ttk.Spinbox(limits, from_=1.00, to=2.00, increment=0.05, format="%.2f", width=7, textvariable=self.video_max_speed).pack(side=tk.LEFT, padx=4)
        ttk.Label(limits, text="x；合并短区间 ≤").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Spinbox(limits, from_=0.20, to=2.00, increment=0.10, format="%.2f", width=7, textvariable=self.merge_short_sec).pack(side=tk.LEFT)
        ttk.Label(limits, text="秒").pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(
            video,
            text="超过范围时仍以英文音频为时间轴，用短暂定格或画面跳转补偿；合成前会列出异常段。",
            foreground="#8a5a00",
        ).pack(anchor=tk.W, padx=(24, 0), pady=(6, 0))

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(18, 0))
        ttk.Button(actions, text="保存设置", command=self.save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="保存并关闭", command=self.save_close).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="退出不保存", command=self.destroy).pack(side=tk.LEFT)
        self.status = ttk.Label(actions, text="")
        self.status.pack(side=tk.RIGHT)

    def _load(self) -> None:
        cfg = load_config()
        compose = cfg.get("compose", {})
        mode = str(compose.get("media_fit_mode", "audio_segment_fit"))
        self.mode.set(mode if mode in {"audio_segment_fit", "video_segment_fit"} else "audio_segment_fit")
        fit = compose.get("video_segment_fit", {})
        self.video_min_speed.set(float(fit.get("min_speed", 0.75)))
        self.video_max_speed.set(float(fit.get("max_speed", 1.35)))
        self.merge_short_sec.set(float(fit.get("merge_short_sec", 0.60)))

    def save(self) -> bool:
        try:
            cfg = load_config()
            compose = cfg.setdefault("compose", {})
            compose["media_fit_mode"] = self.mode.get()
            low = max(0.50, min(1.00, float(self.video_min_speed.get())))
            high = max(1.00, min(2.00, float(self.video_max_speed.get())))
            fit = compose.setdefault("video_segment_fit", {})
            fit.update(
                min_speed=round(low, 2),
                max_speed=round(high, 2),
                merge_short_sec=round(max(0.20, float(self.merge_short_sec.get())), 2),
            )
            save_config(cfg)
            self.status.configure(text="已保存到 config.user.yaml", foreground="#167c2d")
            return True
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return False

    def save_close(self) -> None:
        if self.save():
            self.destroy()


if __name__ == "__main__":
    AudioFitEditor().mainloop()
