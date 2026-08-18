from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from src.app_controller import AppController
from src import __version__


ROOT = Path(__file__).resolve().parent


def main() -> int:
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(sys.argv)
    app.setApplicationName(f"StoryCut V2 {__version__}")
    app.setOrganizationName("SubDub Forge")

    engine = QQmlApplicationEngine()
    controller = AppController(ROOT)
    engine.rootContext().setContextProperty("appController", controller)
    engine.load(QUrl.fromLocalFile(str(ROOT / "ui" / "Main.qml")))

    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
