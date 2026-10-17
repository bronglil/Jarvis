import os
import socket
import subprocess
import sys
import time

from plugin import alias, plugin

URL = "http://127.0.0.1:8765"


def _root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _already_up():
    probe = socket.socket()
    probe.settimeout(0.4)
    try:
        probe.connect(("127.0.0.1", 8765))
        return True
    except OSError:
        return False
    finally:
        probe.close()


@alias("hud")
@plugin("brong")
def brong(jarvis, s):
    """Open the Brong voice screen. Click the core and speak."""
    if not _already_up():
        server = os.path.join(_root(), "hud", "server.py")
        subprocess.Popen(
            [sys.executable, server],
            cwd=_root(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        for _ in range(50):
            if _already_up():
                break
            time.sleep(0.1)
    if not _already_up():
        jarvis.say("Brong did not start.")
        return
    subprocess.run(["open", URL], check=False)
    jarvis.say("Brong is open. Click the core and speak.")
