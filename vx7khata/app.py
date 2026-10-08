"""Application bootstrap."""
from __future__ import annotations

import sys

from . import APP_NAME, paths


def run() -> int:
    import os

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox

    from . import db, security
    from .service import KhataService
    from .ui import theme
    from .ui.dialogs import LockDialog
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    if os.name == "nt":
        app.setFont(QFont("Segoe UI", 10))

    try:
        service = KhataService(paths.db_path())
    except (db.DatabaseError, OSError) as exc:
        app.setStyleSheet(theme.build_stylesheet("light"))
        QMessageBox.critical(None, APP_NAME, f"The data file could not be opened:\n\n{exc}\n\n{paths.db_path()}")
        return 1

    name = service.get_setting("theme", "light")
    theme.set_current(name)
    app.setStyleSheet(theme.build_stylesheet(name))

    if security.has_pin(service):  # PIN screen comes before any data is shown
        if not LockDialog(service, security.PinGuard(service)).exec():
            service.close()
            return 0

    window = MainWindow(service)
    window.show()
    window.run_startup_tasks()
    code = app.exec()
    service.close()
    return code
