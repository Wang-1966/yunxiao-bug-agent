"""独立预览进程入口：后端管道断开时清理整个进程组。"""
import os
import signal
import subprocess
import sys
import threading


def watch_parent():
    while os.read(0, 1):
        pass
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except ProcessLookupError:
        pass


if __name__ == '__main__':
    threading.Thread(target=watch_parent, daemon=True).start()
    os._exit(subprocess.call(['/bin/sh', '-c', sys.argv[1]]))
