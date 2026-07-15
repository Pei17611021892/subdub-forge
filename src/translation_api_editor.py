from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from config_manager import load_config, save_config


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"


def read_env() -> tuple[list[str], dict[str, str]]:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return lines, values


def update_env(lines: list[str], updates: dict[str, str]) -> None:
    remaining = dict(updates)
    result: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in remaining:
            result.append(f"{key}={remaining.pop(key)}")
        else:
            result.append(raw)
    if result and result[-1].strip():
        result.append("")
    for key, value in remaining.items():
        result.append(f"{key}={value}")
    temp = ENV_FILE.with_suffix(".env.tmp")
    temp.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")
    os.replace(temp, ENV_FILE)


class TranslationApiEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("翻译 API 设置")
        self.geometry("720x390")
        self.minsize(660, 350)
        self.api_key = tk.StringVar()
        self.base_url = tk.StringVar()
        self.model = tk.StringVar(value="gpt-4o-mini")
        self.show_key = tk.BooleanVar(value=False)
        self._build()
        self._load()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, text="翻译 API 设置", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(
            root,
            text="用于第二步将中文字幕翻译并压缩为英文。API Key 保存到项目 .env，接口和模型保存到 config.user.yaml，均不会显示在运行日志中。",
            foreground="#555", wraplength=670,
        ).pack(anchor=tk.W, pady=(4, 16))

        form = ttk.Frame(root)
        form.pack(fill=tk.X)
        ttk.Label(form, text="API Key", width=14).grid(row=0, column=0, sticky=tk.W, pady=6)
        self.key_entry = ttk.Entry(form, textvariable=self.api_key, show="●")
        self.key_entry.grid(row=0, column=1, sticky=tk.EW, pady=6)
        ttk.Checkbutton(form, text="显示", variable=self.show_key, command=self._toggle_key).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(form, text="接口地址", width=14).grid(row=1, column=0, sticky=tk.W, pady=6)
        ttk.Entry(form, textvariable=self.base_url).grid(row=1, column=1, columnspan=2, sticky=tk.EW, pady=6)
        ttk.Label(form, text="官方 OpenAI 可留空；兼容接口请填写完整地址。", foreground="#666").grid(row=2, column=1, columnspan=2, sticky=tk.W)

        ttk.Label(form, text="翻译模型", width=14).grid(row=3, column=0, sticky=tk.W, pady=(12, 6))
        ttk.Entry(form, textvariable=self.model).grid(row=3, column=1, columnspan=2, sticky=tk.EW, pady=(12, 6))
        form.columnconfigure(1, weight=1)

        ttk.Label(
            root,
            text="注意：.env 仍是明文文件，请不要把它发送给别人或上传到公开仓库。",
            foreground="#8a5a00",
        ).pack(anchor=tk.W, pady=(14, 0))

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(18, 0))
        ttk.Button(actions, text="保存", command=self.save).pack(side=tk.LEFT)
        ttk.Button(actions, text="保存并关闭", command=self.save_close).pack(side=tk.LEFT, padx=8)
        ttk.Button(actions, text="取消", command=self.destroy).pack(side=tk.LEFT)
        self.status = ttk.Label(actions, text="")
        self.status.pack(side=tk.RIGHT)

    def _load(self) -> None:
        _lines, env = read_env()
        cfg = load_config()
        trans = cfg.get("translate", {})
        self.api_key.set(env.get("OPENAI_API_KEY", ""))
        self.base_url.set(str(trans.get("base_url", "") or env.get("OPENAI_BASE_URL", "")))
        self.model.set(str(trans.get("model", "gpt-4o-mini")))

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key.get() else "●")

    def save(self) -> bool:
        key = self.api_key.get().strip()
        model = self.model.get().strip()
        if not key:
            messagebox.showwarning("缺少 API Key", "请输入翻译接口的 API Key。")
            return False
        if not model:
            messagebox.showwarning("缺少模型", "请输入翻译模型名称，例如 gpt-4o-mini。")
            return False
        try:
            lines, _env = read_env()
            update_env(lines, {"OPENAI_API_KEY": key, "OPENAI_BASE_URL": self.base_url.get().strip()})
            cfg = load_config()
            trans = cfg.setdefault("translate", {})
            trans["provider"] = "openai"
            trans["model"] = model
            trans["base_url"] = self.base_url.get().strip()
            save_config(cfg)
            self.status.configure(text="已保存", foreground="#167c2d")
            return True
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return False

    def save_close(self) -> None:
        if self.save():
            self.destroy()


if __name__ == "__main__":
    TranslationApiEditor().mainloop()
