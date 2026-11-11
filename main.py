"""界面入口。"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

# ---- 必须在导入任何第三方库之前：禁止子进程弹出黑色控制台窗口 ----
if sys.platform == "win32":
    _OrigPopen = subprocess.Popen

    class _NoWindowPopen(_OrigPopen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = (kwargs.get("creationflags", 0)
                                       | subprocess.CREATE_NO_WINDOW)
            si = kwargs.get("startupinfo") or subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0
            kwargs["startupinfo"] = si
            kwargs.setdefault("stdin", subprocess.DEVNULL)
            if sys.stdout is None:
                kwargs.setdefault("stdout", subprocess.DEVNULL)
                kwargs.setdefault("stderr", subprocess.DEVNULL)
            try:
                logging.info("[popen] %s", args[0] if args else kwargs.get("args"))
            except Exception:
                pass
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoWindowPopen

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from PySide6.QtCore import QSettings, Qt

from app.download import ensure_hf_endpoint
from ui.main_window import MainWindow
from ui.theme import theme


def main() -> int:
    if getattr(sys, "frozen", False):
        log_path = Path(sys.executable).resolve().parent / "SpeakerSplit.log"
        try:
            stream = open(log_path, "a", encoding="utf-8", buffering=1)
            sys.stdout = stream
            sys.stderr = stream
        except Exception:
            log_path = None
        if log_path is not None:
            logging.basicConfig(filename=str(log_path), level=logging.INFO,
                                format="%(asctime)s %(message)s")

    ensure_hf_endpoint()

    def _log_uncaught(exc_type, exc, tb):
        logging.error("未捕获异常", exc_info=(exc_type, exc, tb))

    sys.excepthook = _log_uncaught

    import threading

    def _log_thread_uncaught(args):
        logging.error("线程未捕获异常",
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    threading.excepthook = _log_thread_uncaught

    if "--diarize" in sys.argv:
        from app.diarize_cli import main as diarize_main
        return diarize_main(sys.argv[sys.argv.index("--diarize") + 1:])

    # 传一个媒体文件参数时不开界面，直接跑完整流程（便于批量处理/自检）
    files = [a for a in sys.argv[1:] if not a.startswith("-")]
    if files and Path(files[0]).is_file():
        from PySide6.QtCore import QCoreApplication, QThread

        from app.pipeline import run as run_pipeline

        source = Path(files[0])
        out_dir = source.with_name(source.stem + "_output")
        clean = "--no-clean" not in sys.argv

        class _Runner(QThread):
            def __init__(self):
                super().__init__()
                self.setStackSize(64 * 1024 * 1024)

            def run(self):
                try:
                    run_pipeline(source, out_dir, clean=clean)
                except Exception as exc:
                    logging.exception("[batch] %s", exc)
                finally:
                    QCoreApplication.quit()

        app = QCoreApplication(sys.argv)
        runner = _Runner()
        runner.start()
        app.exec()
        runner.wait()
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("SpeakerSplit")

    from app.resources import icon_ico

    icon = icon_ico()
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "SpeakerSplit.App")
        except Exception:
            pass

    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"])
    font.setPixelSize(13)
    app.setFont(font)

    settings = QSettings("SpeakerSplit", "SpeakerSplit")
    mode = settings.value("appearance", "system")
    if mode == "dark":
        theme.apply(True)
    elif mode == "light":
        theme.apply(False)
    else:
        hints = app.styleHints()
        theme.apply(hints.colorScheme() == Qt.ColorScheme.Dark)
        hints.colorSchemeChanged.connect(
            lambda scheme: theme.apply(scheme == Qt.ColorScheme.Dark))
    app.setStyleSheet(theme.qss())

    from ui.watchdog import MainThreadWatchdog
    app._watchdog = MainThreadWatchdog()

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    raise SystemExit(main())
