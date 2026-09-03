"""The memory cap covers the whole process group, not just the direct child.

The smoke matrix runs each cell under `tools/memcap.py`, and the codex
route spawns a `codex app-server` sidecar (`codex_client._spawn_codex`).
A cap that read only the direct child's RSS could not see the sidecar's
memory at all, and a kill that signalled only the direct child left the
sidecar running with launchd as its parent — the exact leak the cap
exists to prevent. The group is the unit because it survives the cell
exiting: a sidecar the cell forgot to reap is still in the group, and
`killpg` reaches it without a pid list that could go stale.
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

SLEEPER = """
import os, sys, time
with open(os.path.join(sys.argv[1], "sleeper.pid"), "w") as handle:
    handle.write(str(os.getpid()))
time.sleep(60)
"""

# the same spawn, and then the parent leaves at once: the memory lives on
# in a process the cap's direct child no longer has any relation to.
# argv: out dir, grandchild script, exit status.
DESERTING_PARENT = """
import os, subprocess, sys, time
out, script, status = sys.argv[1], sys.argv[2], int(sys.argv[3])
subprocess.Popen([sys.executable, os.path.join(out, script), out])
with open(os.path.join(out, "parent.pid"), "w") as handle:
    handle.write(str(os.getpid()))
# leave only once the grandchild has said who it is, so the test has a pid
# to look for however fast the sweep is
pid_path = os.path.join(out, script.replace(".py", ".pid"))
deadline = time.monotonic() + 5
while not os.path.exists(pid_path) and time.monotonic() < deadline:
    time.sleep(0.02)
raise SystemExit(status)
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
def test_the_cap_measures_and_kills_the_whole_group(tmp_path):
    """The parent is small and the grandchild is huge: nothing is killed at
    all unless the cap adds up the group, and the grandchild survives the
    kill unless the kill reaches the group."""
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


def _desert(tmp_path, script, body, limit, status=0):
    (tmp_path / script).write_text(body)
    (tmp_path / "parent.py").write_text(DESERTING_PARENT)
    return subprocess.run(
        [
            sys.executable,
            str(MEMCAP),
            "--limit-mb",
            str(limit),
            "--",
            sys.executable,
            str(tmp_path / "parent.py"),
            str(tmp_path),
            script,
            str(status),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=_plain_env(),
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -axo layout is macOS's")
def test_memory_left_behind_by_a_departed_child_is_still_cleaned_up(tmp_path):
    """The command exits immediately and leaves its grandchild holding
    300 MB. Waiting on the direct child is not enough: the memory is over
    the cap whether it was read while the parent still lived or only in
    the sweep afterwards, so it is a breach either way — 137, said out
    loud, and no process left holding it. A sweep that measured the
    survivors but never compared them to the limit reported a 312 MB peak
    and exited 0."""
    done = _desert(tmp_path, "grandchild.py", GRANDCHILD, limit=150, status=0)

    grandchild = int((tmp_path / "grandchild.pid").read_text())
    assert _wait_gone([grandchild]) == [], "the abandoned memory outlived the cap"
    assert done.returncode == 137, done.stderr
    assert "killed past 150 MB" in done.stderr
    assert "memcap: peak" in done.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="ps -axo layout is macOS's")
def test_a_survivor_under_the_cap_is_swept_without_failing_the_cell(tmp_path):
    """A leftover process that never came near the limit is still swept and
    still said out loud — but it is not a breach, so the cell's own exit
    status is what comes back."""
    done = _desert(tmp_path, "sleeper.py", SLEEPER, limit=150, status=5)

    sleeper = int((tmp_path / "sleeper.pid").read_text())
    assert _wait_gone([sleeper]) == [], "the leftover process was not swept"
    assert done.returncode == 5, done.stderr
    assert "memcap: killed 1 survivor(s) after exit" in done.stderr
    assert "killed past" not in done.stderr
