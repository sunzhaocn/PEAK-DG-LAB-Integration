"""Run runtime regressions in isolated processes without game/device I/O."""
from pathlib import Path
import os
import subprocess
import sys
import textwrap


def run_isolated(script, *, ui=False, timeout=30):
    source = Path(__file__).resolve().parents[1] / "src" / "Coyote"
    prelude = f"""
import atexit
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import urllib.request
sys.path.insert(0, {str(source)!r})
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
def forbidden_network(*args, **kwargs):
    raise OSError('Network disabled by regression test')
socket.socket.connect = forbidden_network
socket.socket.connect_ex = forbidden_network
socket.socket.bind = forbidden_network
socket.create_connection = forbidden_network
urllib.request.urlopen = forbidden_network
import backend as B
temporary = tempfile.TemporaryDirectory(prefix='coyote-regression-')
atexit.register(temporary.cleanup)
root = Path(temporary.name)
B.ROOT = root
B.DOC_DIR = root / 'docs'
B.CONFIG_FILE = root / 'config.json'
B.LOG_DIR = root / 'logs'
B.EVENT_LOG_FILE = root / 'logs' / 'events.jsonl'
original_thread_start = threading.Thread.start
threading.Thread.start = lambda self: None
try:
    from coyote_app import bootstrap
    bootstrap.install_backend_extensions()
finally:
    threading.Thread.start = original_thread_start
V = bootstrap.VIS
EXT = bootstrap.EXT
MP = bootstrap.MP
"""
    if ui:
        prelude += """
from coyote_app.ui import qt as UI
bootstrap.install_ui_extensions(UI)
app = UI.QApplication([])
"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(prelude) + textwrap.dedent(script)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout,
    )
    if result.returncode:
        raise AssertionError(
            f"Regression process exited {result.returncode}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout
