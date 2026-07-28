from __future__ import annotations

import os
import queue
import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from config_manager import load_config, save_config
from update_manager import check_for_update, download_and_apply

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
PIPELINE = ROOT / "src" / "pipeline.py"
STYLE_EDITOR = ROOT / "src" / "style_editor.py"
AUDIO_FIT_EDITOR = ROOT / "src" / "audio_fit_editor.py"
TTS_AUDIO_EDITOR = ROOT / "src" / "tts_audio_editor.py"
TRANSLATION_API_EDITOR = ROOT / "src" / "translation_api_editor.py"
LAUNCHER_VBS = REPOSITORY_ROOT / "点我启动翻译工具.vbs"
OUTPUT = ROOT / "output"
INTERNAL = OUTPUT / "_internal"
VERSION_FILE = ROOT / "version.json"


def app_version() -> str:
    try:
        return str(json.loads(VERSION_FILE.read_text(encoding="utf-8")).get("version", "1.0.0"))
    except Exception:
        return "1.0.0"

FILES = {
    "audio": INTERNAL / "audio.wav",
    "zh": OUTPUT / "zh.srt",
    "en": OUTPUT / "en_raw.srt",
    "voice": OUTPUT / "en_voice.wav",
    "synced_srt": OUTPUT / "en_synced.srt",
    "final": OUTPUT / "final.mp4",
}


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"声画译匠 · SubDub Forge v{app_version()}")
        self.geometry("980x680")
        self.minsize(860, 560)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.running = False
        self.worker: threading.Thread | None = None
        self.status_labels: dict[str, ttk.Label] = {}
        self.update_button: ttk.Button | None = None
        self._update_check_running = False
        self._cached_update_result: tuple[dict, dict, bool] | None = None

        self._build_ui()
        self.refresh_status()
        self.after(100, self._drain_log_queue)
        self.after(1200, self._start_silent_update_check)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(root, text=f"声画译匠 · SubDub Forge  v{app_version()}", font=("Microsoft YaHei UI", 16, "bold"))
        title.pack(anchor=tk.W)

        video_bar = ttk.LabelFrame(root, text="当前项目视频", padding=8)
        video_bar.pack(fill=tk.X, pady=(8, 6))
        self.video_path_label = ttk.Label(video_bar, text="", foreground="#333")
        self.video_path_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(video_bar, text="选择 / 更换视频", command=self.select_video).pack(side=tk.RIGHT)

        help_text = (
            "推荐流程：1 识别中文字幕 → 2 翻译英文字幕 → 用 TTS 生成音频和同步 SRT → 3 导入并处理 TTS 音频 → 设置字幕/音频拷入 → 4 合成视频。\n"
            "第三步优先让 TTS 音频与其同步 SRT 一起变速；没有 TTS SRT 时可选择 Whisper 重新识别。"
        )
        ttk.Label(root, text=help_text, wraplength=930, justify=tk.LEFT).pack(anchor=tk.W, pady=(6, 12))

        status_frame = ttk.LabelFrame(root, text="文件状态", padding=10)
        status_frame.pack(fill=tk.X)

        rows = [
            ("audio", "提取音频（内部）", "output/_internal/audio.wav"),
            ("zh", "中文字幕", "output/zh.srt"),
            ("en", "英文字幕", "output/en_raw.srt"),
            ("voice", "英文配音 wav", "output/en_voice.wav"),
            ("synced_srt", "TTS 英文字幕时间轴", "output/en_synced.srt，与处理后配音同步，必需"),
            ("final", "最终视频", "output/final.mp4"),
        ]
        for r, (key, name, path_text) in enumerate(rows):
            ttk.Label(status_frame, text=name, width=18).grid(row=r, column=0, sticky=tk.W, padx=(0, 8), pady=2)
            lab = ttk.Label(status_frame, text="检测中", width=18)
            lab.grid(row=r, column=1, sticky=tk.W, padx=(0, 8), pady=2)
            ttk.Label(status_frame, text=path_text).grid(row=r, column=2, sticky=tk.W, pady=2)
            self.status_labels[key] = lab
        status_frame.columnconfigure(2, weight=1)

        btn_frame = ttk.LabelFrame(root, text="操作", padding=10)
        btn_frame.pack(fill=tk.X, pady=(12, 8))

        self.buttons: list[ttk.Button] = []
        buttons = [
            ("一键智能运行/继续", self.smart_run),
            ("检查项目", self.check_project),
            ("1 识别中文字幕", lambda: self.run_pipeline_step("asr")),
            ("2 翻译英文字幕", lambda: self.run_pipeline_step("translate")),
            ("翻译 API 设置", self.open_translation_api_editor),
            ("3 导入并处理 TTS 音频", self.open_tts_audio_editor),
            ("刷新文件状态", self.refresh_status),
            ("字幕位置与样式设置", self.open_style_editor),
            ("音频拷入设置", self.open_audio_fit_editor),
            ("4 合成最终视频", lambda: self.run_pipeline_step("compose")),
            ("检查应用更新", self.check_app_update),
            ("打开 output 文件夹", self.open_output),
            ("清空日志", self.clear_log),
        ]
        for i, (text, cmd) in enumerate(buttons):
            b = ttk.Button(btn_frame, text=text, command=cmd)
            b.grid(row=i // 4, column=i % 4, padx=5, pady=5, sticky=tk.EW)
            self.buttons.append(b)
            if text == "检查应用更新":
                self.update_button = b
        for c in range(4):
            btn_frame.columnconfigure(c, weight=1)

        log_frame = ttk.LabelFrame(root, text="运行日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.log_text = tk.Text(log_frame, height=18, wrap=tk.WORD, font=("Consolas", 10))
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_status(self) -> None:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        video = self._input_video()
        self.video_path_label.configure(
            text=str(video) if video else "尚未选择视频",
            foreground="#167c2d" if video and video.exists() else "#b42318",
        )
        for key, path in FILES.items():
            label = self.status_labels[key]
            if path.exists():
                size = path.stat().st_size
                mtime = time.strftime("%m-%d %H:%M", time.localtime(path.stat().st_mtime))
                label.configure(text=f"✅ 已存在 {self._fmt_size(size)} {mtime}")
            else:
                label.configure(text="❌ 未找到")
        self._log("已刷新文件状态。")

    def _load_config(self) -> dict:
        return load_config()

    def _save_config(self, cfg: dict) -> None:
        save_config(cfg)

    def _input_video(self) -> Path | None:
        try:
            raw = str(self._load_config().get("input_video", "")).strip()
            if not raw:
                return None
            path = Path(raw)
            return path.resolve() if path.is_absolute() else (ROOT / path).resolve()
        except Exception:
            return None

    @staticmethod
    def _video_fingerprint(path: Path) -> str:
        stat = path.stat()
        raw = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def select_video(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择要翻译的视频",
            filetypes=[("视频文件", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        new_video = Path(selected).resolve()
        old_video = self._input_video()
        changed = old_video is None or old_video != new_video
        generated = [p for key, p in FILES.items() if key != "audio" and p.exists()]
        if changed and generated:
            if not messagebox.askokcancel(
                "更换项目视频",
                "检测到当前项目已有字幕、配音或成片。更换视频后这些文件不能继续混用。\n\n"
                "确定后会把现有用户文件备份到 output/_internal/project_backups，再开始新项目。",
                icon="warning",
            ):
                return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = INTERNAL / "project_backups" / stamp
            backup.mkdir(parents=True, exist_ok=True)
            for path in generated:
                shutil.move(str(path), str(backup / path.name))
            audio = FILES["audio"]
            if audio.exists():
                audio.unlink()
        cfg = self._load_config()
        cfg["input_video"] = str(new_video)
        cfg.setdefault("project", {})["input_fingerprint"] = self._video_fingerprint(new_video)
        self._save_config(cfg)
        self._log(f"已选择项目视频：{new_video}")
        self.refresh_status()

    def check_project(self) -> None:
        problems: list[str] = []
        notes: list[str] = []
        video = self._input_video()
        if not video or not video.exists():
            problems.append("未选择有效的输入视频，请先点击“选择 / 更换视频”。")
        else:
            cfg = self._load_config()
            saved = str(cfg.get("project", {}).get("input_fingerprint", ""))
            current = self._video_fingerprint(video)
            if saved and saved != current:
                problems.append("输入视频已被替换或修改，现有产物可能属于旧视频；请重新选择该视频以建立新项目。")
            notes.append(f"输入视频：{video.name}（{self._fmt_size(video.stat().st_size)}）")
        if FILES["en"].exists() and not FILES["zh"].exists():
            problems.append("存在 en_raw.srt，但缺少它所依据的 zh.srt。")
        if FILES["voice"].exists() != FILES["synced_srt"].exists():
            problems.append("en_voice.wav 与 en_synced.srt 不完整，请重新执行第三步导出。")
        if FILES["final"].exists() and video and FILES["final"].stat().st_mtime < video.stat().st_mtime:
            problems.append("final.mp4 早于当前输入视频，可能是旧成片。")
        ready = not self._compose_missing_message()
        notes.append("合成文件：已齐备" if ready else "合成文件：尚未齐备（这在前期步骤中是正常的）")
        text = ("发现以下问题：\n\n" + "\n".join(f"• {x}" for x in problems) + "\n\n" if problems else "未发现项目混用或文件完整性问题。\n\n") + "\n".join(notes)
        messagebox.showwarning("项目检查", text) if problems else messagebox.showinfo("项目检查", text)

    def _fmt_size(self, size: int) -> str:
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        return f"{size / 1024 / 1024:.1f}MB"

    def set_running(self, running: bool) -> None:
        self.running = running
        for b in self.buttons:
            b.configure(state=tk.DISABLED if running else tk.NORMAL)

    def _start_worker(self, target) -> None:
        if self.running:
            messagebox.showinfo("正在运行", "已有任务正在运行，请等它结束。")
            return
        self.set_running(True)
        def guarded_target() -> None:
            try:
                target()
            except Exception as exc:
                self._log(f"任务失败：{exc}")
                self._log("请查看上方最后几行，那里包含 FFmpeg/Pipeline 的具体错误。")

        self.worker = threading.Thread(target=guarded_target, daemon=True)
        self.worker.start()

    def run_pipeline_step(self, step: str) -> None:
        if step == "compose":
            missing = self._compose_missing_message()
            if missing:
                messagebox.showwarning("无法合成：缺少文件", missing)
                return
        self._start_worker(lambda: self._worker_run_step(step))

    def _compose_missing_message(self) -> str:
        missing: list[str] = []
        if not FILES["en"].exists():
            missing.append("• output/en_raw.srt：请先点击“2 翻译英文字幕”生成。")
        if not FILES["voice"].exists():
            missing.append("• output/en_voice.wav：请点击“3 导入并处理 TTS 音频”，选择 GPT-SoVITS 音频后导出。")
        if not FILES["synced_srt"].exists():
            missing.append("• output/en_synced.srt：请在第三步导出音频时自动生成。")
        return "合成最终视频需要以下文件：\n\n" + "\n".join(missing) if missing else ""

    def smart_run(self) -> None:
        self._start_worker(self._worker_smart_run)

    def _worker_run_step(self, step: str) -> None:
        try:
            self._run_cmd([sys.executable, str(PIPELINE), "--step", step])
        finally:
            self.log_queue.put("__REFRESH__")
            self.log_queue.put("__DONE__")

    def _worker_smart_run(self) -> None:
        try:
            self._log("开始一键智能运行/继续。")
            OUTPUT.mkdir(parents=True, exist_ok=True)

            if not FILES["zh"].exists():
                self._log("未发现 output/zh.srt，开始：识别中文字幕。")
                self._run_cmd([sys.executable, str(PIPELINE), "--step", "asr"])
            else:
                self._log("已发现 output/zh.srt，跳过 ASR。")

            if not FILES["en"].exists():
                self._log("未发现 output/en_raw.srt，开始：翻译英文字幕。")
                self._run_cmd([sys.executable, str(PIPELINE), "--step", "translate"])
            else:
                self._log("已发现 output/en_raw.srt，跳过翻译。")

            missing = self._compose_missing_message()
            if not missing:
                self._log("已发现 output/en_voice.wav，开始：合成最终视频。")
                self._run_cmd([sys.executable, str(PIPELINE), "--step", "compose"])
                self._log("一键智能运行完成。")
            else:
                self._log("暂停：合成所需文件尚未齐备。")
                for line in missing.splitlines():
                    self._log(line)
                self.after(0, lambda text=missing: messagebox.showwarning("请先完成 TTS 音频处理", text))
        finally:
            self.log_queue.put("__REFRESH__")
            self.log_queue.put("__DONE__")

    def _run_cmd(self, cmd: list[str]) -> None:
        self._log("RUN: " + " ".join(cmd))
        child_env = os.environ.copy()
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        child_env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        recent_lines: list[str] = []
        for line in proc.stdout:
            clean_line = line.rstrip()
            self._log(clean_line)
            if clean_line:
                recent_lines.append(clean_line)
                recent_lines = recent_lines[-12:]
        code = proc.wait()
        if code != 0:
            self._log(f"命令失败，退出码：{code}")
            detail = recent_lines[-1] if recent_lines else f"退出码 {code}"
            raise RuntimeError(f"命令失败（退出码 {code}）：{detail}")

    def _log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if msg == "__DONE__":
                    self.set_running(False)
                    continue
                if msg == "__REFRESH__":
                    self.refresh_status()
                    continue
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def check_app_update(self) -> None:
        if self._cached_update_result and self._cached_update_result[2]:
            local, remote, newer = self._cached_update_result
            self._show_update_result(local, remote, newer)
            return
        self._start_worker(lambda: self._worker_check_update(silent=False))

    def _start_silent_update_check(self) -> None:
        if self._update_check_running:
            return
        self._update_check_running = True
        threading.Thread(
            target=lambda: self._worker_check_update(silent=True),
            name="translator-update-check",
            daemon=True,
        ).start()

    def _worker_check_update(self, silent: bool) -> None:
        try:
            if not silent:
                self._log("正在连接 GitHub 检查应用更新……")
            local, remote, newer = check_for_update()
            self._cached_update_result = (local, remote, newer)
            if silent:
                self.after(0, lambda: self._apply_silent_update_result(remote, newer))
            else:
                self._log(f"本地版本：v{local['version']}；GitHub 版本：v{remote['version']}")
                self.after(200, lambda: self._show_update_result(local, remote, newer))
        except Exception as exc:
            error = str(exc)
            if not silent:
                self._log(f"检查更新失败：{error}")
                self.after(200, lambda: messagebox.showerror("检查更新失败", error))
        finally:
            self._update_check_running = False
            if not silent:
                self.log_queue.put("__DONE__")

    def _apply_silent_update_result(self, remote: dict, newer: bool) -> None:
        if self.update_button is None:
            return
        if newer:
            self.update_button.configure(text=f"↑ 可更新 v{remote.get('version', '')}")
        else:
            self.update_button.configure(text="检查应用更新")

    def _show_update_result(self, local: dict, remote: dict, newer: bool) -> None:
        if not newer:
            messagebox.showinfo("检查更新", f"当前版本 v{local['version']} 已是最新版。")
            return
        notes = str(remote.get("notes", "")).strip() or "GitHub 上有新的稳定版本。"
        confirmed = messagebox.askyesno(
            "发现新版本",
            f"当前版本：v{local['version']}\n最新版本：v{remote['version']}\n\n{notes}\n\n"
            "更新会备份并替换程序文件，不会修改 .env、config.user.yaml、models 或 output。\n\n现在下载并安装吗？",
            icon="info",
        )
        if confirmed:
            self._start_worker(lambda: self._worker_apply_update(remote))

    def _worker_apply_update(self, remote: dict) -> None:
        try:
            backup = download_and_apply(remote, self._log)
            self.after(200, lambda: self._update_complete(str(remote["version"]), backup))
        except Exception as exc:
            error = str(exc)
            self._log(f"应用更新失败：{error}")
            self.after(200, lambda: messagebox.showerror("更新失败", error))
        finally:
            self.log_queue.put("__DONE__")

    def _update_complete(self, version: str, backup: Path) -> None:
        restart = messagebox.askyesno(
            "更新完成",
            f"声画译匠已更新到 v{version}。\n\n旧程序备份：\n{backup}\n\n必须重启后才能使用新版。现在重启吗？",
        )
        if not restart:
            return
        try:
            os.startfile(str(LAUNCHER_VBS))  # type: ignore[attr-defined]
            self.destroy()
        except Exception as exc:
            messagebox.showerror("无法自动重启", f"请手动关闭并双击“点我启动翻译工具.vbs”。\n\n{exc}")

    def open_style_editor(self) -> None:
        try:
            subprocess.Popen([sys.executable, str(STYLE_EDITOR)], cwd=str(ROOT))
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def open_audio_fit_editor(self) -> None:
        try:
            subprocess.Popen([sys.executable, str(AUDIO_FIT_EDITOR)], cwd=str(ROOT))
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def open_translation_api_editor(self) -> None:
        try:
            subprocess.Popen([sys.executable, str(TRANSLATION_API_EDITOR)], cwd=str(ROOT))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))

    def open_tts_audio_editor(self) -> None:
        source = filedialog.askopenfilename(
            title="选择 GPT-SoVITS 生成的音频",
            filetypes=[("音频文件", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg"), ("所有文件", "*.*")],
        )
        if not source:
            return
        try:
            proc = subprocess.Popen([sys.executable, str(TTS_AUDIO_EDITOR), "--input", source], cwd=str(ROOT))
            def refresh_when_closed() -> None:
                proc.wait()
                self.log_queue.put("__REFRESH__")
            threading.Thread(target=refresh_when_closed, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))

    def open_output(self) -> None:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(OUTPUT))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))


if __name__ == "__main__":
    app = Launcher()
    app.mainloop()
