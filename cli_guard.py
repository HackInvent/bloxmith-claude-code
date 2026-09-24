"""Tie a Claude subprocess tree to its block host using a lifetime pipe.

Always establish a new POSIX session here, before launching any CLI or thread.
Only that private group may be stopped; the Studio process group is never used.
"""

import os
import signal
import subprocess
import sys
import threading


def main():
    """Supervise Claude even when the framework force-stops the owning hook."""
    os.setsid()
    owned_group = os.getpid()
    if os.getpgrp() != owned_group:
        raise RuntimeError("Claude guard requires its own process group.")
    lifetime_fd = int(sys.argv[1])

    def watch_owner():
        try:
            while os.read(lifetime_fd, 1):
                pass
        finally:
            # The validated, newly created group contains only this call's CLI.
            os.killpg(owned_group, signal.SIGKILL)

    threading.Thread(target=watch_owner, daemon=True).start()
    try:
        process = subprocess.Popen(sys.argv[3:], pass_fds=(int(sys.argv[2]),))
    except OSError:
        print("Claude Code executable could not be started. Check the configured binary.", file=sys.stderr)
        return 127
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
