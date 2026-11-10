import logging
import sys
import threading
import time
import traceback

from PySide6.QtCore import QTimer


class MainThreadWatchdog:
    """主线程卡住超过阈值时，把调用栈写进日志。"""

    def __init__(self, threshold: float = 0.2):
        self.threshold = threshold
        self.last = time.perf_counter()
        self.main_id = threading.main_thread().ident
        self.timer = QTimer()
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._beat)
        self.timer.start()
        threading.Thread(target=self._watch, daemon=True).start()

    def _beat(self):
        self.last = time.perf_counter()

    def _watch(self):
        reported = False
        while True:
            time.sleep(0.05)
            lag = time.perf_counter() - self.last
            if lag > self.threshold and not reported:
                frame = sys._current_frames().get(self.main_id)
                stack = "".join(traceback.format_stack(frame)) if frame else "?"
                logging.warning("[stall] UI 卡住 %.0fms，主线程位于:\n%s",
                                lag * 1000, stack)
                reported = True
            elif lag <= self.threshold:
                reported = False
