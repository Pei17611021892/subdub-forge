from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from src.update_manager import migrate_repository


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parent
NEW_MAIN = REPOSITORY_ROOT / "storycut_v2" / "main.py"


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, "StoryCut 自动迁移", 0x10)


def main() -> int:
    try:
        migrate_repository()
        if not NEW_MAIN.exists():
            raise RuntimeError("更新完成后仍找不到 storycut_v2/main.py")
        subprocess.Popen([sys.executable, str(NEW_MAIN)], cwd=str(REPOSITORY_ROOT))
        return 0
    except Exception as exc:
        show_error(f"旧版自动迁移失败：\n\n{exc}\n\n请确认 Clash 已开启系统代理后重试。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
