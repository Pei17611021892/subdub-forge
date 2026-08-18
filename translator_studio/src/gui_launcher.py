from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from update_manager import migrate_repository


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = ROOT.parent
NEW_LAUNCHER = REPOSITORY_ROOT / "storycut_v1" / "src" / "gui_launcher.py"


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, "StoryCut V1 自动迁移", 0x10)


def main() -> int:
    try:
        migrate_repository()
        if not NEW_LAUNCHER.exists():
            raise RuntimeError("更新完成后仍找不到 storycut_v1/src/gui_launcher.py")
        subprocess.Popen([sys.executable, str(NEW_LAUNCHER)], cwd=str(REPOSITORY_ROOT))
        return 0
    except Exception as exc:
        show_error(f"旧版自动迁移失败：\n\n{exc}\n\n请检查网络后重新启动。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
