from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import base64
import colorsys
import threading
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from subtitle_cleanup import build_cleanup_graph, cleanup_mode
from config_manager import load_config, save_config

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
INTERNAL = OUTPUT / "_internal"

DEFAULT_STYLE = {
    "FontName": "Arial",
    "FontSize": "30",
    "PrimaryColour": "&H00FFFFFF",
    "OutlineColour": "&H00000000",
    "BackColour": "&H90000000",
    "BorderStyle": "1",
    "Outline": "3",
    "Shadow": "1",
    "Alignment": "2",
    "MarginV": "34",
    "Bold": "1",
}

PRESETS = {
    "黑色遮罩": {
        "cleanup_mode": "mask",
        "cover": {"x": "iw*0.08", "y": "ih*0.85", "w": "iw*0.84", "h": "ih*0.13", "color": "black@0.88"},
        "style": {**DEFAULT_STYLE, "FontSize": "28", "PrimaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000", "BorderStyle": "1", "Outline": "2", "Shadow": "1", "MarginV": "18", "Bold": "1"},
    },
    "局部柔化": {
        "cleanup_mode": "blur",
        "cover": {"x": "iw*0.08", "y": "ih*0.85", "w": "iw*0.84", "h": "ih*0.13", "color": "black@0.00"},
        "style": {**DEFAULT_STYLE, "FontSize": "28", "PrimaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000", "BorderStyle": "1", "Outline": "1", "Shadow": "1", "MarginV": "18", "Bold": "1"},
    },
    "Delogo 修复": {
        "cleanup_mode": "delogo",
        "cover": {"x": "iw*0.08", "y": "ih*0.85", "w": "iw*0.84", "h": "ih*0.13", "color": "black@0.00"},
        "style": {**DEFAULT_STYLE, "FontSize": "28", "PrimaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000", "BorderStyle": "1", "Outline": "0", "Shadow": "1", "MarginV": "18", "Bold": "1"},
        "animation": "fade",
    },
}

RECOMMENDED_SUBTITLE_FONTS = [
    "Segoe UI", "Arial", "Calibri", "Verdana", "Tahoma", "Trebuchet MS",
    "Georgia", "Garamond", "Times New Roman", "Century Gothic",
    "Franklin Gothic Medium", "Microsoft YaHei UI", "Microsoft YaHei",
    "方正舒体",
]


def parse_force_style(style: str) -> dict[str, str]:
    result = dict(DEFAULT_STYLE)
    for part in (style or "").split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def build_force_style(style: dict[str, str]) -> str:
    order = [
        "FontName", "FontSize", "PrimaryColour", "OutlineColour", "BackColour",
        "BorderStyle", "Outline", "Shadow", "Alignment", "MarginV", "MarginL", "MarginR", "Bold", "Italic",
    ]
    parts = []
    for key in order:
        value = str(style.get(key, "")).strip()
        if value != "":
            parts.append(f"{key}={value}")
    for key, value in style.items():
        if key not in order and str(value).strip():
            parts.append(f"{key}={value}")
    return ",".join(parts)


def ass_to_rgba(value: str) -> str:
    """Convert ASS &HAABBGGRR (inverted alpha) to #RRGGBBAA."""
    m = re.match(r"&H([0-9A-Fa-f]{8})$", value.strip())
    if not m:
        return "#FFFFFFFF"
    raw = m.group(1)
    ass_alpha, bb, gg, rr = raw[0:2], raw[2:4], raw[4:6], raw[6:8]
    rgba_alpha = 255 - int(ass_alpha, 16)
    return f"#{rr}{gg}{bb}{rgba_alpha:02X}".upper()


def rgba_to_ass(rgba: str) -> str:
    """Convert #RRGGBBAA to ASS &HAABBGGRR."""
    h = rgba.strip().lstrip("#")
    if len(h) == 6:
        h += "FF"
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", h):
        h = "FFFFFFFF"
    rr, gg, bb, rgba_alpha = h[0:2], h[2:4], h[4:6], h[6:8]
    ass_alpha = 255 - int(rgba_alpha, 16)
    return f"&H{ass_alpha:02X}{bb}{gg}{rr}".upper()


def escape_subtitles_path_for_ffmpeg(path: Path) -> str:
    s = path.resolve().as_posix()
    if re.match(r"^[A-Za-z]:/", s):
        s = s[0] + r"\:" + s[2:]
    return s.replace("'", r"\'")


class RgbaColorDialog(tk.Toplevel):
    """Small cross-platform RGBA picker whose colour field commits on every click."""

    def __init__(self, parent: tk.Misc, initial: str, title: str) -> None:
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.result: str | None = None
        self._field_w, self._field_h = 300, 170
        value = initial if re.fullmatch(r"#[0-9A-Fa-f]{8}", initial) else "#FFFFFFFF"
        r, g, b, a = (int(value[i:i + 2], 16) for i in (1, 3, 5, 7))
        self.hue, self.saturation, brightness = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self.brightness = tk.IntVar(value=round(brightness * 100))
        self.alpha = tk.IntVar(value=a)
        self.rgba = tk.StringVar(value=value.upper())

        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)
        ttk.Label(root, text="点击色域直接选择颜色", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor=tk.W)
        self.field = tk.Canvas(root, width=self._field_w, height=self._field_h, highlightthickness=1, highlightbackground="#777")
        self.field.pack(pady=(8, 10))
        self._field_image = tk.PhotoImage(width=self._field_w, height=self._field_h)
        self.field.create_image(0, 0, image=self._field_image, anchor=tk.NW)
        self._marker = self.field.create_oval(0, 0, 0, 0, outline="white", width=2)
        self.field.bind("<Button-1>", self._pick_field)
        self.field.bind("<B1-Motion>", self._pick_field)
        self._draw_field()

        sliders = ttk.Frame(root)
        sliders.pack(fill=tk.X)
        ttk.Label(sliders, text="亮度", width=8).grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Scale(sliders, from_=0, to=100, variable=self.brightness, command=lambda _v: self._update_value()).grid(row=0, column=1, sticky=tk.EW)
        self.brightness_label = ttk.Label(sliders, width=5)
        self.brightness_label.grid(row=0, column=2, padx=(6, 0))
        ttk.Label(sliders, text="透明度", width=8).grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Scale(sliders, from_=0, to=255, variable=self.alpha, command=lambda _v: self._update_value()).grid(row=1, column=1, sticky=tk.EW)
        self.alpha_label = ttk.Label(sliders, width=5)
        self.alpha_label.grid(row=1, column=2, padx=(6, 0))
        sliders.columnconfigure(1, weight=1)

        exact = ttk.Frame(root)
        exact.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(exact, text="RGBA", width=8).pack(side=tk.LEFT)
        entry = ttk.Entry(exact, textvariable=self.rgba, width=14)
        entry.pack(side=tk.LEFT)
        entry.bind("<Return>", self._entry_commit)
        entry.bind("<FocusOut>", self._entry_commit)
        self.swatch = tk.Label(exact, width=8, relief=tk.SUNKEN, bd=1)
        self.swatch.pack(side=tk.LEFT, padx=(10, 0), fill=tk.Y)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(14, 0))
        ttk.Button(actions, text="确定", command=self._accept).pack(side=tk.LEFT)
        ttk.Button(actions, text="取消", command=self.destroy).pack(side=tk.LEFT, padx=8)
        self._update_value()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        self.wait_visibility()
        self.focus_set()

    def _draw_field(self) -> None:
        rows: list[str] = []
        for y in range(self._field_h):
            saturation = y / max(1, self._field_h - 1)
            colors = []
            for x in range(self._field_w):
                hue = x / max(1, self._field_w - 1)
                r, g, b = colorsys.hsv_to_rgb(hue, saturation, 1.0)
                colors.append(f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}")
            rows.append("{" + " ".join(colors) + "}")
        self._field_image.put(" ".join(rows))

    def _pick_field(self, event: tk.Event) -> None:
        x = max(0, min(self._field_w - 1, int(event.x)))
        y = max(0, min(self._field_h - 1, int(event.y)))
        self.hue = x / max(1, self._field_w - 1)
        self.saturation = y / max(1, self._field_h - 1)
        self._update_value()

    def _update_value(self) -> None:
        brightness = max(0.0, min(1.0, self.brightness.get() / 100))
        alpha = max(0, min(255, self.alpha.get()))
        r, g, b = colorsys.hsv_to_rgb(self.hue, self.saturation, brightness)
        rgba = f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}{alpha:02X}"
        self.rgba.set(rgba)
        self.swatch.configure(bg=rgba[:7])
        self.brightness_label.configure(text=f"{round(brightness * 100)}%")
        self.alpha_label.configure(text=str(alpha))
        x = self.hue * (self._field_w - 1)
        y = self.saturation * (self._field_h - 1)
        self.field.coords(self._marker, x - 5, y - 5, x + 5, y + 5)
        self.field.itemconfigure(self._marker, outline="black" if brightness > 0.75 and self.saturation < 0.35 else "white")

    def _entry_commit(self, _event=None) -> None:
        value = self.rgba.get().strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{8}", value):
            return
        r, g, b, a = (int(value[i:i + 2], 16) for i in (1, 3, 5, 7))
        self.hue, self.saturation, brightness = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        self.brightness.set(round(brightness * 100))
        self.alpha.set(a)
        self._update_value()

    def _accept(self) -> None:
        self.result = self.rgba.get().upper()
        self.destroy()


class StyleEditor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("字幕位置与样式设置")
        self.geometry("1400x900")
        self.minsize(1180, 760)
        self.cfg = load_config()
        self.vars: dict[str, tk.StringVar | tk.BooleanVar] = {}
        self.scales: dict[str, ttk.Scale] = {}
        self.color_swatches: dict[str, tk.Label] = {}
        self._preview_job: str | None = None
        self._preview_serial = 0
        self._preview_image: tk.PhotoImage | None = None
        self._video_size: tuple[int, int] | None = None
        self._drag_start: tuple[float, float] | None = None
        self._drag_origin: tuple[float, float, float] | None = None
        self._compare_original = False
        self._preview_ratio = tk.StringVar(value="9:16")
        self._loading = True
        self._build()
        self.load_from_config()
        self._loading = False
        self.after(250, self._start_preview)

    def _build(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        ttk.Label(main, text="字幕位置与样式设置", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor=tk.W)
        ttk.Label(
            main,
            text="左侧为实时预览：拖动位置或修改样式会自动更新；满意后再保存到 config.user.yaml。",
            wraplength=1180,
        ).pack(anchor=tk.W, pady=(4, 10))

        actions = ttk.Frame(main)
        actions.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(actions, text="保存设置", command=self.save_to_config).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="保存并关闭", command=self.save_and_close).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="退出不保存", command=self.destroy).pack(side=tk.LEFT)
        self.save_status = ttk.Label(actions, text="预览自动更新，配置尚未修改", foreground="#666")
        self.save_status.pack(side=tk.RIGHT)

        top = ttk.Frame(main)
        top.pack(fill=tk.BOTH, expand=True)
        preview_side = ttk.Frame(top)
        settings_side = ttk.Frame(top)
        preview_side.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        settings_side.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))

        settings_tabs = ttk.Notebook(settings_side)
        settings_tabs.pack(fill=tk.BOTH, expand=True)
        cover_box = ttk.Frame(settings_tabs, padding=10)
        settings_tabs.add(cover_box, text="原字幕擦除")
        self._combo(cover_box, "cleanup_mode", "处理方式", "delogo", 0, [
            ("mask", "黑色遮罩（最彻底）"),
            ("blur", "局部柔化/模糊（推荐）"),
            ("delogo", "Delogo 周边像素修复"),
        ])
        self.vars["cleanup_mode"].trace_add("write", self._cleanup_mode_changed)
        ttk.Label(cover_box, text="所选处理模式会从视频开始到结束始终连续显示", foreground="#666").grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(2, 4))
        self._slider(cover_box, "cover_y_pct", "区域顶部", 74, 0, 100, 2, "%")
        self._slider(cover_box, "cover_h_pct", "区域高度", 26, 1, 100, 3, "%")
        self._slider(cover_box, "cover_x_pct", "区域左侧", 0, 0, 100, 4, "%")
        self._slider(cover_box, "cover_w_pct", "区域宽度", 100, 1, 100, 5, "%")
        self._slider(cover_box, "cover_opacity", "遮罩不透明度", 92, 0, 100, 6, "%")
        self._slider(cover_box, "blur_radius", "柔化强度", 12, 1, 40, 7, "")
        self._slider(cover_box, "blur_power", "柔化层次", 2, 1, 4, 8, "")
        self._slider(cover_box, "region_padding", "区域向外扩展", 4, 0, 80, 9, "px")
        self._slider(cover_box, "feather", "模糊柔边扩展", 12, 0, 60, 10, "px")
        ttk.Label(cover_box, text="也可在右侧预览画面拖动擦除区域（只移动位置，不改变大小）", foreground="#666").grid(row=11, column=0, columnspan=3, sticky=tk.W, pady=(2, 4))

        style_box = ttk.Frame(settings_tabs, padding=10)
        settings_tabs.add(style_box, text="文字样式")
        self._font_picker(style_box, 0)
        self._slider(style_box, "FontSize", "字号", 30, 12, 72, 1, "")
        self._color_row(style_box, "PrimaryColour", "文字颜色", 2)
        self._slider(style_box, "MarginV", "垂直边距", 36, 0, 240, 3, "px")
        self._combo(style_box, "Alignment", "位置", "2", 4, [("2", "底部居中"), ("5", "正中"), ("8", "顶部居中")])
        self._combo(style_box, "BorderStyle", "边框/背景", "1", 5, [("1", "普通描边"), ("3", "文字背景盒（使用下方描边/文字盒颜色）")])
        self._slider(style_box, "Outline", "描边/盒边", 3, 0, 10, 6, "")
        self._color_row(style_box, "OutlineColour", "描边/文字盒颜色", 7)
        self._slider(style_box, "Shadow", "阴影", 1, 0, 10, 8, "")
        self._bool(style_box, "BoldBool", "粗体", 9)
        self._bool(style_box, "ItalicBool", "斜体", 10)
        self._slider(style_box, "Spacing", "字间距", 0, -5, 20, 11, "")
        self._combo(style_box, "subtitle_animation", "字幕动画", "none", 12, [
            ("none", "无（最稳定）"),
            ("fade", "柔和淡入淡出（约 0.14 秒）"),
            ("pop", "轻微缩放进入（约 0.17 秒）"),
        ])
        ttk.Label(style_box, text="颜色格式：#RRGGBBAA，末两位 AA 表示透明度", foreground="#666").grid(row=13, column=0, columnspan=4, sticky=tk.W, pady=(5, 2))
        self.font_sample = tk.Label(style_box, text="Aa 字幕效果 Preview 预览", anchor=tk.CENTER, relief=tk.GROOVE, bg="#252525", fg="#ffffff", pady=7)
        self.font_sample.grid(row=14, column=0, columnspan=4, sticky=tk.EW, pady=(7, 2))

        preset_box = ttk.LabelFrame(settings_side, text="擦除模式预设", padding=10)
        preset_box.pack(fill=tk.X, pady=(10, 0))
        self.preset_var = tk.StringVar(value=list(PRESETS.keys())[0])
        for column, name in enumerate(PRESETS):
            ttk.Button(
                preset_box, text=name,
                command=lambda selected=name: (self.preset_var.set(selected), self.apply_preset()),
            ).grid(row=0, column=column, sticky=tk.EW, padx=(0 if column == 0 else 4, 0))
            preset_box.columnconfigure(column, weight=1)

        preview_box = ttk.LabelFrame(preview_side, text="实时预览（无需保存）", padding=10)
        preview_box.pack(fill=tk.BOTH, expand=True)
        toolbar = ttk.Frame(preview_box)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        self.vars["preview_time"] = tk.StringVar(value="12")
        ttk.Label(toolbar, text="视频时间").pack(side=tk.LEFT)
        time_spin = ttk.Spinbox(toolbar, from_=0, to=99999, increment=1, width=8, textvariable=self.vars["preview_time"], command=lambda: self.schedule_preview(False))
        time_spin.pack(side=tk.LEFT, padx=(6, 3))
        time_spin.bind("<KeyRelease>", lambda _event: self.schedule_preview(False))
        ttk.Label(toolbar, text="秒").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="刷新", command=lambda: self.schedule_preview(False)).pack(side=tk.LEFT, padx=10)
        ttk.Label(toolbar, text="预览比例").pack(side=tk.LEFT, padx=(6, 4))
        ttk.Radiobutton(
            toolbar, text="竖屏 9:16", value="9:16", variable=self._preview_ratio,
            command=self._preview_ratio_changed,
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            toolbar, text="宽屏 16:9", value="16:9", variable=self._preview_ratio,
            command=self._preview_ratio_changed,
        ).pack(side=tk.LEFT, padx=(4, 0))
        self.compare_button = ttk.Button(toolbar, text="点击看原画", command=self.toggle_compare)
        self.compare_button.pack(side=tk.LEFT, padx=(0, 8))
        self.preview_status = ttk.Label(toolbar, text="正在准备…", foreground="#666")
        self.preview_status.pack(side=tk.RIGHT)
        # The outer host may grow with the window, while the dark inner surface is
        # the actual fixed-ratio preview area (Douyin-style player viewport).
        self.preview_host = tk.Frame(preview_box, bg="#303030", height=480)
        self.preview_host.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas = tk.Label(self.preview_host, text="正在加载视频预览…", bg="#181818", fg="#dddddd", anchor=tk.CENTER)
        preview_w, preview_h = self._preview_target_size()
        self.preview_canvas.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=preview_w, height=preview_h)
        self.preview_canvas.bind("<ButtonPress-1>", self._preview_drag_start)
        self.preview_canvas.bind("<B1-Motion>", self._preview_drag_move)
        self.preview_canvas.bind("<ButtonRelease-1>", self._preview_drag_end)
        ttk.Label(preview_box, text="提示：上下拖动文字或背景会整体移动擦除区域和字幕；滑块右侧可输入精确数值。", foreground="#666").pack(anchor=tk.W, pady=(7, 0))

        self.log = tk.Text(main, height=4, wrap=tk.WORD, font=("Consolas", 9))
        self.log.pack(fill=tk.BOTH, expand=False, pady=(10, 0))

    def _entry(self, parent, key: str, label: str, default: str, row: int, hint: str = "") -> None:
        var = tk.StringVar(value=default)
        self.vars[key] = var
        ttk.Label(parent, text=label, width=14).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky=tk.EW, pady=3)
        ttk.Label(parent, text=hint, foreground="#666").grid(row=row, column=2, sticky=tk.W, padx=(6, 0), pady=3)
        parent.columnconfigure(1, weight=1)
        var.trace_add("write", lambda *_args: self.schedule_preview())

    def _font_picker(self, parent, row: int) -> None:
        installed = sorted({name for name in tkfont.families(self) if not name.startswith("@")}, key=str.casefold)
        self.installed_fonts = installed
        preferences = self.cfg.get("ui", {}).get("style_editor", {})
        self.font_favorites = [name for name in preferences.get("font_favorites", []) if name in installed]
        self.recent_fonts = [name for name in preferences.get("recent_fonts", []) if name in installed][:10]
        values = self._ordered_fonts()
        var = tk.StringVar(value="Arial")
        self.vars["FontName"] = var
        ttk.Label(parent, text="字体", width=14).grid(row=row, column=0, sticky=tk.W, pady=3)
        box = ttk.Combobox(parent, textvariable=var, values=values, state="normal")
        box.grid(row=row, column=1, sticky=tk.EW, pady=3)
        ttk.Button(parent, text=f"字体库（{len(installed)}）", command=self.open_font_browser).grid(row=row, column=2, sticky=tk.EW, padx=(6, 0), pady=3)
        parent.columnconfigure(1, weight=1)

        def changed(*_args) -> None:
            if hasattr(self, "font_sample"):
                try:
                    self.font_sample.configure(font=(str(var.get()), 17))
                except tk.TclError:
                    pass
            self.schedule_preview()

        var.trace_add("write", changed)
        def selected(_event=None) -> None:
            self._remember_font(str(var.get()))
            changed()
        box.bind("<<ComboboxSelected>>", selected)

    def _ordered_fonts(self, query: str = "") -> list[str]:
        query_folded = query.strip().casefold()
        current = str(self.vars.get("FontName", tk.StringVar(value="")).get()) if "FontName" in self.vars else ""
        groups = [
            [current],
            getattr(self, "font_favorites", []),
            getattr(self, "recent_fonts", []),
            [name for name in RECOMMENDED_SUBTITLE_FONTS if name in getattr(self, "installed_fonts", [])],
            getattr(self, "installed_fonts", []),
        ]
        ordered: list[str] = []
        seen: set[str] = set()
        for group in groups:
            for name in group:
                if not name or name in seen or name not in getattr(self, "installed_fonts", []):
                    continue
                if query_folded and query_folded not in name.casefold():
                    continue
                seen.add(name)
                ordered.append(name)
        return ordered

    def _save_font_preferences(self) -> None:
        cfg = load_config()
        editor = cfg.setdefault("ui", {}).setdefault("style_editor", {})
        editor["font_favorites"] = self.font_favorites
        editor["recent_fonts"] = self.recent_fonts[:10]
        save_config(cfg)
        self.cfg = cfg

    def _remember_font(self, name: str) -> None:
        if name not in getattr(self, "installed_fonts", []):
            return
        self.recent_fonts = [name] + [font for font in self.recent_fonts if font != name]
        self.recent_fonts = self.recent_fonts[:10]
        self._save_font_preferences()

    def open_font_browser(self) -> None:
        popup = tk.Toplevel(self)
        popup.title("字体库：搜索、悬停预览与收藏")
        popup.geometry("620x600")
        popup.minsize(520, 480)
        popup.transient(self)

        root = ttk.Frame(popup, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        search_var = tk.StringVar()
        search = ttk.Entry(root, textvariable=search_var)
        search.pack(fill=tk.X)
        search.insert(0, "")
        ttk.Label(root, text="输入名称筛选；鼠标悬停看样张，单击后更新视频预览。", foreground="#666").pack(anchor=tk.W, pady=(4, 8))

        list_frame = ttk.Frame(root)
        list_frame.pack(fill=tk.BOTH, expand=True)
        font_list = tk.Listbox(list_frame, activestyle="dotbox", exportselection=False, font=("Microsoft YaHei UI", 10))
        scrollbar = ttk.Scrollbar(list_frame, command=font_list.yview)
        font_list.configure(yscrollcommand=scrollbar.set)
        font_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        sample = tk.Label(
            root,
            text="The future begins today.\nSubtitle Style Preview\n中文字幕样式预览",
            bg="#242424", fg="#ffffff", relief=tk.GROOVE, pady=14, justify=tk.CENTER,
        )
        sample.pack(fill=tk.X, pady=(10, 0))
        selected_font = tk.StringVar(value=str(self.vars["FontName"].get()))
        visible_fonts: list[str] = []

        def marker(name: str) -> str:
            if name in self.font_favorites:
                return f"★ {name}"
            if name in self.recent_fonts:
                return f"最近  {name}"
            if name in RECOMMENDED_SUBTITLE_FONTS:
                return f"推荐  {name}"
            return f"      {name}"

        def show_sample(name: str) -> None:
            if not name:
                return
            try:
                sample.configure(font=(name, 20))
            except tk.TclError:
                sample.configure(font=("Arial", 20))

        def refresh(*_args) -> None:
            nonlocal visible_fonts
            visible_fonts = self._ordered_fonts(search_var.get())
            font_list.delete(0, tk.END)
            for name in visible_fonts:
                font_list.insert(tk.END, marker(name))
            current = str(self.vars["FontName"].get())
            if current in visible_fonts:
                index = visible_fonts.index(current)
                font_list.selection_set(index)
                font_list.see(index)

        def hovered(event: tk.Event) -> None:
            if not visible_fonts or font_list.size() == 0:
                return
            index = font_list.nearest(event.y)
            if 0 <= index < len(visible_fonts):
                show_sample(visible_fonts[index])

        def selected(_event=None) -> None:
            indexes = font_list.curselection()
            if not indexes:
                return
            name = visible_fonts[indexes[0]]
            selected_font.set(name)
            show_sample(name)
            self.vars["FontName"].set(name)
            self._remember_font(name)

        def toggle_favorite() -> None:
            indexes = font_list.curselection()
            name = visible_fonts[indexes[0]] if indexes and indexes[0] < len(visible_fonts) else str(self.vars["FontName"].get())
            if name not in self.installed_fonts:
                return
            if name in self.font_favorites:
                self.font_favorites.remove(name)
            else:
                self.font_favorites.insert(0, name)
            self._save_font_preferences()
            refresh()

        font_list.bind("<Motion>", hovered)
        font_list.bind("<<ListboxSelect>>", selected)
        font_list.bind("<Double-Button-1>", lambda _event: popup.destroy())
        search_var.trace_add("write", refresh)

        actions = ttk.Frame(root)
        actions.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(actions, text="收藏 / 取消收藏", command=toggle_favorite).pack(side=tk.LEFT)
        ttk.Button(actions, text="关闭", command=popup.destroy).pack(side=tk.RIGHT)
        refresh()
        selected_font.set(str(self.vars["FontName"].get()))
        show_sample(str(self.vars["FontName"].get()))
        search.focus_set()

    def _cleanup_mode_changed(self, *_args) -> None:
        if self._loading or str(self.vars["cleanup_mode"].get()) != "delogo":
            return
        # Delogo needs a little surrounding image on every side for interpolation.
        try:
            if float(str(self.vars["cover_x_pct"].get())) <= 1 and float(str(self.vars["cover_w_pct"].get())) >= 99:
                self.vars["cover_x_pct"].set("8")
                self.vars["cover_w_pct"].set("84")
        except (TypeError, ValueError):
            pass

    def _slider(self, parent, key: str, label: str, default: int, low: int, high: int, row: int, suffix: str) -> None:
        step = 0.1 if suffix == "%" else 1
        var = tk.StringVar(value=str(default))
        self.vars[key] = var
        ttk.Label(parent, text=label, width=14).grid(row=row, column=0, sticky=tk.W, pady=3)
        scale = ttk.Scale(parent, from_=low, to=high, orient=tk.HORIZONTAL)
        self.scales[key] = scale
        scale.set(default)
        scale.grid(row=row, column=1, sticky=tk.EW, pady=3)
        exact = ttk.Frame(parent)
        exact.grid(row=row, column=2, sticky=tk.E, padx=(6, 0), pady=3)
        number_box = ttk.Spinbox(exact, from_=low, to=high, increment=step, width=7, textvariable=var, command=self.schedule_preview)
        number_box.pack(side=tk.LEFT)
        ttk.Label(exact, text=suffix, width=max(1, min(4, len(suffix))), anchor=tk.W).pack(side=tk.LEFT, padx=(2, 0))

        def commit_number(_event=None) -> None:
            try:
                number = max(low, min(high, float(var.get())))
            except (TypeError, ValueError, tk.TclError):
                number = float(default)
            var.set(f"{number:.1f}" if step < 1 else str(int(round(number))))
            self.schedule_preview()

        number_box.bind("<Return>", commit_number)
        number_box.bind("<FocusOut>", commit_number)

        def changed(value: str) -> None:
            number = round(float(value), 1) if step < 1 else int(round(float(value)))
            var.set(f"{number:.1f}" if step < 1 else str(number))

        scale.configure(command=changed)
        parent.columnconfigure(1, weight=1)
        def variable_changed(*_args) -> None:
            try:
                wanted = float(var.get())
                if abs(float(scale.get()) - wanted) > 0.51:
                    scale.set(wanted)
            except (TypeError, ValueError, tk.TclError):
                pass
            self.schedule_preview()

        var.trace_add("write", variable_changed)

    def _bool(self, parent, key: str, label: str, row: int) -> None:
        var = tk.BooleanVar(value=True)
        self.vars[key] = var
        ttk.Checkbutton(parent, text=label, variable=var, command=self.schedule_preview).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=3)

    def _combo(self, parent, key: str, label: str, default: str, row: int, values: list[tuple[str, str]]) -> None:
        var = tk.StringVar(value=default)
        self.vars[key] = var
        ttk.Label(parent, text=label, width=14).grid(row=row, column=0, sticky=tk.W, pady=3)
        display = [f"{v} - {name}" for v, name in values]
        box = ttk.Combobox(parent, values=display, state="readonly")
        box.set(next((d for d in display if d.startswith(default + " ")), display[0]))
        box.grid(row=row, column=1, sticky=tk.EW, pady=3)
        box.bind("<<ComboboxSelected>>", lambda _e: var.set(box.get().split(" ", 1)[0]))
        ttk.Label(parent, text="", foreground="#666").grid(row=row, column=2, sticky=tk.W, padx=(6, 0), pady=3)

        def variable_changed(*_args) -> None:
            selected = next((d for d in display if d.startswith(str(var.get()) + " ")), display[0])
            if box.get() != selected:
                box.set(selected)
            self.schedule_preview()

        var.trace_add("write", variable_changed)

    def _color_row(self, parent, key: str, label: str, row: int) -> None:
        var = tk.StringVar(value=ass_to_rgba(DEFAULT_STYLE.get(key, "&H00FFFFFF")))
        self.vars[key] = var
        ttk.Label(parent, text=label, width=14).grid(row=row, column=0, sticky=tk.W, pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky=tk.EW, pady=3)
        swatch = tk.Label(parent, width=3, relief=tk.SUNKEN, bd=1, bg=var.get()[0:7])
        swatch.grid(row=row, column=2, sticky=tk.NS, padx=(6, 2), pady=3)
        self.color_swatches[key] = swatch
        ttk.Button(parent, text="选择", command=lambda: self.pick_color(key)).grid(row=row, column=3, sticky=tk.EW, padx=(4, 0), pady=3)
        parent.columnconfigure(1, weight=1)

        def changed(*_args) -> None:
            value = str(var.get()).strip()
            if re.fullmatch(r"#[0-9A-Fa-f]{8}", value):
                swatch.configure(bg=value[0:7])
            self.schedule_preview()

        var.trace_add("write", changed)

    def pick_color(self, key: str) -> None:
        current = str(self.vars[key].get()).strip()
        dialog = RgbaColorDialog(self, current, "选择文字颜色" if key == "PrimaryColour" else "选择描边/文字盒颜色")
        self.wait_window(dialog)
        if dialog.result:
            self.vars[key].set(dialog.result)

    @staticmethod
    def _expression_percent(value: object, axis: str, default: int) -> int:
        text = str(value).strip().lower().replace(" ", "")
        if text in (axis, "1*" + axis):
            return 100
        if text in ("0", "0.0"):
            return 0
        match = re.fullmatch(rf"{axis}\*([0-9.]+)", text)
        if match:
            return max(0, min(100, round(float(match.group(1)) * 100)))
        return default

    def load_from_config(self) -> None:
        comp = self.cfg.get("compose", {})
        cover = comp.get("cover", {})
        cleanup = comp.get("subtitle_cleanup", {})
        loaded_mode = cleanup_mode(comp)
        self.vars["cleanup_mode"].set("delogo" if loaded_mode == "none" else loaded_mode)
        self.vars["blur_radius"].set(str(cleanup.get("blur_radius", 12)))
        self.vars["blur_power"].set(str(cleanup.get("blur_power", 2)))
        self.vars["region_padding"].set(str(cleanup.get("region_padding", 4)))
        self.vars["feather"].set(str(cleanup.get("feather", 12)))
        self.vars["cover_x_pct"].set(str(self._expression_percent(cover.get("x", 0), "iw", 0)))
        self.vars["cover_y_pct"].set(str(self._expression_percent(cover.get("y", "ih*0.74"), "ih", 74)))
        self.vars["cover_w_pct"].set(str(self._expression_percent(cover.get("w", "iw"), "iw", 100)))
        self.vars["cover_h_pct"].set(str(self._expression_percent(cover.get("h", "ih*0.26"), "ih", 26)))
        color = str(cover.get("color", "black@0.92"))
        opacity_match = re.search(r"@([0-9.]+)", color)
        self.vars["cover_opacity"].set(str(round(float(opacity_match.group(1)) * 100) if opacity_match else 100))
        style = parse_force_style(comp.get("subtitle_style", ""))
        if str(style.get("FontName", "")).startswith("@"):
            style["FontName"] = str(style["FontName"])[1:]
        for key, value in style.items():
            if key in self.vars:
                self.vars[key].set(ass_to_rgba(value) if key in ("PrimaryColour", "OutlineColour") else value)
        self.vars["BoldBool"].set(str(style.get("Bold", "1")) not in ("0", "false", "False"))
        self.vars["ItalicBool"].set(str(style.get("Italic", "0")) not in ("0", "false", "False"))
        self.vars["subtitle_animation"].set(str(comp.get("subtitle_animation", "none")))

    def current_style(self) -> dict[str, str]:
        keys = ["FontName", "FontSize", "BorderStyle", "Outline", "Shadow", "Alignment", "MarginV", "Spacing"]
        style = {k: str(self.vars[k].get()).strip() for k in keys if k in self.vars}
        style["FontName"] = style.get("FontName", "Arial").lstrip("@") or "Arial"
        style["PrimaryColour"] = rgba_to_ass(str(self.vars["PrimaryColour"].get()))
        style["OutlineColour"] = rgba_to_ass(str(self.vars["OutlineColour"].get()))
        style["BackColour"] = "&H90000000"  # soft semi-transparent black shadow
        style["Bold"] = "1" if bool(self.vars["BoldBool"].get()) else "0"
        style["Italic"] = "1" if bool(self.vars["ItalicBool"].get()) else "0"
        return style

    def apply_preset(self) -> None:
        preset = PRESETS[self.preset_var.get()]
        cover = preset["cover"]
        self.vars["cleanup_mode"].set(str(preset.get("cleanup_mode", "delogo")))
        self.vars["cover_y_pct"].set(str(self._expression_percent(cover["y"], "ih", 74)))
        self.vars["cover_h_pct"].set(str(self._expression_percent(cover["h"], "ih", 26)))
        self.vars["cover_x_pct"].set(str(self._expression_percent(cover.get("x", "0"), "iw", 0)))
        self.vars["cover_w_pct"].set(str(self._expression_percent(cover.get("w", "iw"), "iw", 100)))
        opacity_match = re.search(r"@([0-9.]+)", cover["color"])
        self.vars["cover_opacity"].set(str(round(float(opacity_match.group(1)) * 100) if opacity_match else 100))
        for key, value in preset["style"].items():
            if key in self.vars:
                self.vars[key].set(ass_to_rgba(value) if key in ("PrimaryColour", "OutlineColour") else value)
        self.vars["BoldBool"].set(str(preset["style"].get("Bold", "1")) != "0")
        self.vars["ItalicBool"].set(str(preset["style"].get("Italic", "0")) != "0")
        self.vars["subtitle_animation"].set(str(preset.get("animation", "none")))
        self._log(f"已应用预设：{self.preset_var.get()}")

    def save_to_config(self) -> bool:
        invalid_colors = [
            str(self.vars[key].get()) for key in ("PrimaryColour", "OutlineColour")
            if not re.fullmatch(r"#[0-9A-Fa-f]{8}", str(self.vars[key].get()).strip())
        ]
        if invalid_colors:
            messagebox.showerror("颜色格式错误", "颜色必须使用 #RRGGBBAA 格式，例如 #FFFFFFFF。")
            return False
        cfg = load_config()
        comp = cfg.setdefault("compose", {})
        mode = str(self.vars["cleanup_mode"].get())
        comp["cover_original_subtitle"] = True
        cleanup = comp.setdefault("subtitle_cleanup", {})
        cleanup["mode"] = mode
        cleanup["blur_radius"] = int(float(str(self.vars["blur_radius"].get())))
        cleanup["blur_power"] = int(float(str(self.vars["blur_power"].get())))
        cleanup["region_padding"] = int(float(str(self.vars["region_padding"].get())))
        cleanup["feather"] = int(float(str(self.vars["feather"].get())))
        cover = comp.setdefault("cover", {})
        cover["x"] = f"iw*{float(str(self.vars['cover_x_pct'].get())) / 100:.3f}"
        cover["y"] = f"ih*{float(str(self.vars['cover_y_pct'].get())) / 100:.3f}"
        cover["w"] = f"iw*{float(str(self.vars['cover_w_pct'].get())) / 100:.3f}"
        cover["h"] = f"ih*{float(str(self.vars['cover_h_pct'].get())) / 100:.3f}"
        cover["color"] = f"black@{float(str(self.vars['cover_opacity'].get())) / 100:.3f}"
        comp["subtitle_style"] = build_force_style(self.current_style())
        comp["subtitle_animation"] = str(self.vars["subtitle_animation"].get())
        save_config(cfg)
        self.cfg = cfg
        self._log("已保存到 config.user.yaml")
        self.save_status.configure(text="已保存到 config.user.yaml", foreground="#167c2d")
        return True

    def save_and_close(self) -> None:
        if self.save_to_config():
            self.destroy()

    def toggle_compare(self) -> None:
        self._compare_original = not self._compare_original
        self.compare_button.configure(text="返回处理效果" if self._compare_original else "点击看原画")
        self.schedule_preview(False)

    def _preview_ratio_changed(self) -> None:
        self._drag_start = None
        self._drag_origin = None
        width, height = self._preview_target_size()
        self.preview_canvas.place_configure(width=width, height=height)
        self.schedule_preview(False)

    def _preview_target_size(self) -> tuple[int, int]:
        # Keep portrait previews tall enough to judge subtitle placement without
        # pushing the controls/log outside the default 820 px window.
        return (720, 405) if self._preview_ratio.get() == "16:9" else (360, 640)

    def _preview_point(self, event: tk.Event) -> tuple[float, float] | None:
        if not self._preview_image:
            return None
        image_w, image_h = self._preview_image.width(), self._preview_image.height()
        left = (self.preview_canvas.winfo_width() - image_w) / 2
        top = (self.preview_canvas.winfo_height() - image_h) / 2
        # The source always fills the viewport width. A short scaled frame is
        # centred with top/bottom padding; a tall frame is centre-cropped vertically.
        content_left = left
        content_w = float(image_w)
        if self._video_size:
            source_w, source_h = self._video_size
            fit = image_w / source_w
            scaled_h = source_h * fit
            if scaled_h <= image_h:
                content_top = top + (image_h - scaled_h) / 2
                content_h = scaled_h
                crop_top = 0.0
            else:
                content_top = top
                content_h = float(image_h)
                crop_top = (scaled_h - image_h) / 2
        else:
            content_top = top
            content_h = float(image_h)
            scaled_h = float(image_h)
            crop_top = 0.0
        local_x = float(event.x) - content_left
        local_y = float(event.y) - content_top
        if local_x < 0 or local_y < 0 or local_x > content_w or local_y > content_h:
            return None
        x = local_x / content_w
        y = (local_y + crop_top) / scaled_h
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    def _preview_drag_start(self, event: tk.Event) -> None:
        self._drag_start = self._preview_point(event)
        try:
            self._drag_origin = (
                float(str(self.vars["cover_x_pct"].get())) / 100,
                float(str(self.vars["cover_y_pct"].get())) / 100,
                float(str(self.vars["MarginV"].get())),
            )
        except (TypeError, ValueError):
            self._drag_origin = (0.0, 0.74, 36.0)

    def _preview_drag_move(self, event: tk.Event) -> None:
        if not self._drag_start or not self._drag_origin:
            return
        point = self._preview_point(event)
        if not point:
            return
        _start_x, start_y = self._drag_start
        origin_x, origin_y, origin_margin = self._drag_origin
        width = max(0.01, min(1.0, float(str(self.vars["cover_w_pct"].get())) / 100))
        height = max(0.01, min(1.0, float(str(self.vars["cover_h_pct"].get())) / 100))
        new_x = max(0.0, min(1.0 - width, origin_x))
        new_y = max(0.0, min(1.0 - height, origin_y + point[1] - start_y))
        self.vars["cover_x_pct"].set(f"{new_x * 100:.1f}")
        self.vars["cover_y_pct"].set(f"{new_y * 100:.1f}")
        video_height = self._video_size[1] if self._video_size else self._preview_image.height()
        actual_delta_y = (new_y - origin_y) * video_height
        self.vars["MarginV"].set(str(round(max(0.0, min(240.0, origin_margin - actual_delta_y)))))

    def _preview_drag_end(self, event: tk.Event) -> None:
        self._preview_drag_move(event)
        self._drag_start = None
        self._drag_origin = None

    def schedule_preview(self, mark_dirty: bool = True) -> None:
        if self._loading or not hasattr(self, "preview_canvas"):
            return
        if mark_dirty and hasattr(self, "save_status"):
            self.save_status.configure(text="有未保存的修改", foreground="#a65b00")
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(250, self._start_preview)

    def _preview_filter(self, srt: Path) -> str:
        sub_path = escape_subtitles_path_for_ffmpeg(srt)
        style = build_force_style(self.current_style()).replace("'", r"\'")
        target_w, target_h = self._preview_target_size()
        if self._video_size:
            source_w, source_h = self._video_size
            scaled_h = max(2, round(target_w * source_h / source_w))
            scaled_h += scaled_h % 2
            if scaled_h >= target_h:
                fit_and_center = f"scale={target_w}:{scaled_h},crop={target_w}:{target_h}:0:(ih-{target_h})/2"
            else:
                fit_and_center = f"scale={target_w}:{scaled_h},pad={target_w}:{target_h}:0:(oh-ih)/2:color=0x181818"
        else:
            fit_and_center = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=0x181818"
            )
        subtitle_filter = fit_and_center if self._compare_original else f"subtitles='{sub_path}':force_style='{style}',{fit_and_center}"
        x = float(str(self.vars["cover_x_pct"].get()) or "0") / 100
        y = float(str(self.vars["cover_y_pct"].get()) or "74") / 100
        w = float(str(self.vars["cover_w_pct"].get()) or "100") / 100
        h = float(str(self.vars["cover_h_pct"].get()) or "26") / 100
        opacity = float(str(self.vars["cover_opacity"].get()) or "92") / 100
        comp = {
            "cover_original_subtitle": True,
            "cover": {"x": f"iw*{x:.2f}", "y": f"ih*{y:.2f}", "w": f"iw*{w:.2f}", "h": f"ih*{h:.2f}", "color": f"black@{opacity:.2f}"},
            "subtitle_cleanup": {
                "mode": "none" if self._compare_original else str(self.vars["cleanup_mode"].get()),
                "blur_radius": int(float(str(self.vars["blur_radius"].get()) or "12")),
                "blur_power": int(float(str(self.vars["blur_power"].get()) or "2")),
                "region_padding": int(float(str(self.vars["region_padding"].get()) or "4")),
                "feather": int(float(str(self.vars["feather"].get()) or "12")),
            },
        }
        return build_cleanup_graph(comp, subtitle_filter, None, dynamic_timing=False, video_size=self._video_size)

    def _start_preview(self) -> None:
        self._preview_job = None
        try:
            cfg = load_config()
            input_value = str(cfg.get("input_video", "")).strip()
            if not input_value:
                self.preview_status.configure(text="尚未选择输入视频")
                self.preview_canvas.configure(text="请先在主界面选择项目视频", image="")
                return
            input_video = Path(input_value)
            if not input_video.is_absolute():
                input_video = (ROOT / input_video).resolve()
            if not input_video.is_file():
                self.preview_status.configure(text="找不到输入视频")
                self.preview_canvas.configure(text=f"找不到输入视频：\n{input_video}", image="")
                return
            ffmpeg = cfg.get("ffmpeg", {}).get("ffmpeg_bin", "ffmpeg")
            if not Path(ffmpeg).exists():
                ffmpeg = shutil.which(ffmpeg) or ffmpeg
            if self._video_size is None:
                ffprobe = cfg.get("ffmpeg", {}).get("ffprobe_bin", "ffprobe")
                if not Path(ffprobe).exists():
                    ffprobe = shutil.which(ffprobe) or ffprobe
                probe = subprocess.run(
                    [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(input_video)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                )
                size_match = re.search(r"(\d+)x(\d+)", probe.stdout)
                if probe.returncode == 0 and size_match:
                    self._video_size = (int(size_match.group(1)), int(size_match.group(2)))
            seconds = max(0.0, float(str(self.vars["preview_time"].get()) or "0"))
            self._preview_serial += 1
            serial = self._preview_serial
            # Use an always-visible sample instead of depending on whether the chosen
            # video timestamp happens to contain a real caption. Animation is omitted
            # for this still frame so a fade's first invisible frame is never captured.
            INTERNAL.mkdir(parents=True, exist_ok=True)
            cleanup = INTERNAL / f"style_preview_{serial}.srt"
            cleanup.write_text(
                "1\n00:00:00,000 --> 09:59:59,000\nSubtitle Style Preview / 字幕样式预览\n",
                encoding="utf-8",
            )
            subtitle_source = cleanup
            vf = self._preview_filter(subtitle_source)
            cmd = [ffmpeg, "-v", "error", "-ss", str(seconds), "-i", str(input_video), "-filter_complex", vf, "-map", "[v]", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]
            self.preview_status.configure(text="更新中…")
            threading.Thread(target=self._render_preview, args=(serial, cmd, cleanup), daemon=True).start()
        except Exception as exc:
            self.preview_status.configure(text="预览参数有误")
            self._log(f"实时预览失败：{exc}")

    def _render_preview(self, serial: int, cmd: list[str], cleanup: Path | None = None) -> None:
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, timeout=30)
            if proc.returncode != 0 or not proc.stdout:
                error = proc.stderr.decode("utf-8", errors="replace")[-800:] or "FFmpeg 未返回画面"
                self.after(0, lambda: self._show_preview_error(serial, error))
                return
            encoded = base64.b64encode(proc.stdout).decode("ascii")
            self.after(0, lambda: self._show_preview_image(serial, encoded))
        except Exception as exc:
            error = str(exc)
            self.after(0, lambda: self._show_preview_error(serial, error))
        finally:
            if cleanup:
                try:
                    cleanup.unlink(missing_ok=True)
                except OSError:
                    pass

    def _show_preview_image(self, serial: int, encoded: str) -> None:
        if serial != self._preview_serial:
            return
        try:
            self._preview_image = tk.PhotoImage(data=encoded)
            self.preview_canvas.configure(image=self._preview_image, text="")
            self.preview_status.configure(text="已实时更新")
        except tk.TclError as exc:
            self._show_preview_error(serial, str(exc))

    def _show_preview_error(self, serial: int, error: str) -> None:
        if serial != self._preview_serial:
            return
        self.preview_status.configure(text="预览失败")
        self.preview_canvas.configure(image="", text="预览失败，请查看下方日志")
        self._log("实时预览失败：" + error)

    def open_output(self) -> None:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        os.startfile(str(OUTPUT))  # type: ignore[attr-defined]

    def _log(self, msg: str) -> None:
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)


if __name__ == "__main__":
    app = StyleEditor()
    app.mainloop()
