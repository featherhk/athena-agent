"""Prevent two local runtimes from dispatching the same persisted work."""
import sys


class InstanceLock:
    def __init__(self, path):
        self.file = path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Athena 已在运行。请打开现有窗口，不要启动第二个后端。") from None

    def close(self):
        self.file.close()
