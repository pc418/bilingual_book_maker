"""The memory cap covers the process *tree*, not just the direct child.

The smoke matrix runs each cell under `tools/memcap.py`, and the codex
route spawns a `codex app-server` sidecar (`codex_client._spawn_codex`).
A cap that read only the direct child's RSS could not see the sidecar's
memory at all, and a kill that signalled only the direct child left the
sidecar running with launchd as its parent — the exact leak the cap
exists to prevent.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

MEMCAP = Path(__file__).resolve().parent.parent / "tools" / "memcap.py"

GRANDCHILD = """
import os, sys, time
# the pid goes down before the memory goes up: the cap fires while this is
# still allocating, and the test has to know what to look for afterwards
with open(os.path.join(sys.argv[1], "grandchild.pid"), "w") as handle:
    handle.write(str(os.getpid()))
blob = bytearray(300 * 1024 * 1024)
for index in range(0, len(blob), 4096):
    blob[index] = 1  # touch every page, so the memory is really resident
time.sleep(60)
"""

PARENT = """
import os, subprocess, sys, time
out = sys.argv[1]
grandchild = subprocess.Popen(
    [sys.executable, os.path.join(out, "grandchild.py"), out]
)
with open(os.path.join(out, "parent.pid"), "w") as handle:
    handle.write(str(os.getpid()))
time.sleep(60)
"""


def _plain_env():
    """The environment without this suite's PYTHONPATH.

    `tests/hermetic/sitecustomize.py` imports `book_maker.translator` at
    every interpreter startup, which costs ~70 MB a process — enough to
    trip a small cap before the fixture below has allocated anything. The
    processes here are stand-ins for a translation cell, not for this
    suite, so they run as plain pythons.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pids, deadline=3.0):
    """Poll until every pid is gone; a reaped zombie takes a moment."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        still = [pid for pid in pids if _alive(pid)]
        if not still:
            return []
        time.sleep(0.05)
    return [pid for pid in pids if _alive(pid)]


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -axo layout is macOS's")
def test_the_cap_measures_and_kills_the_whole_tree(tmp_path):
    """The parent is small and the grandchild is huge: nothing is killed at
    all unless the cap adds up the tree, and the grandchild survives the
    kill unless the kill walks it."""
    (tmp_path / "grandchild.py").write_text(GRANDCHILD)
    (tmp_path / "parent.py").write_text(PARENT)

    done = subprocess.run(
        [
            sys.executable,
            str(MEMCAP),
            "--limit-mb",
            "150",
            "--",
            sys.executable,
            str(tmp_path / "parent.py"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_plain_env(),
    )

    assert done.returncode == 137, done.stderr
    assert "killed past 150 MB" in done.stderr

    parent = int((tmp_path / "parent.pid").read_text())
    grandchild = int((tmp_path / "grandchild.pid").read_text())
    assert _wait_gone([parent, grandchild]) == [], "a process outlived the cap"


def test_a_command_inside_the_cap_passes_its_status_through():
    done = subprocess.run(
        [
            sys.executable,
            str(MEMCAP),
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(3)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_plain_env(),
    )
    assert done.returncode == 3
    assert "memcap: peak" in done.stderr
    assert "killed past" not in done.stderr
