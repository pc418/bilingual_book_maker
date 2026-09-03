"""Run a command under a resident-memory cap, and report its peak.

    python tools/memcap.py [--limit-mb 1500] -- python make_book.py ...

macOS has no enforceable per-process memory limit (`ulimit -v` is a
no-op), so this polls the RSS of the command *and everything it started*
and kills the whole tree past the cap. Exists because a smoke matrix once
ran eleven translation cells in parallel and one unbounded list
comprehension at write time took a 16 GB machine down before anything
could be seen. Exit status is the child's, or 137 when the cap was hit;
the last line on stderr is the peak in MB either way, so a smoke log
records what a cell costs.

The tree, not the child: the codex route spawns a `codex app-server`
sidecar (`book_maker/codex_client.py`), whose memory does not appear in
its parent's RSS and which would be left running with launchd as its
parent if only the parent were signalled.
"""

import argparse
import os
import signal
import subprocess
import sys
import time


def _process_table():
    """`({ppid: [pid]}, {pid: rss_kb})` for every process on the machine."""
    listing = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss="], capture_output=True, text=True
    ).stdout
    children, resident = {}, {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, ppid, kilobytes = (int(field) for field in fields)
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
        resident[pid] = kilobytes
    return children, resident


def process_tree(pid):
    """`pid` and every descendant, parents before children.

    A pid with no row in `ps` exited between the listing and now, so it is
    dropped along with the subtree it no longer has.
    """
    children, resident = _process_table()
    tree, queue, seen = [], [pid], set()
    while queue:
        current = queue.pop(0)
        if current in seen or current not in resident:
            continue
        seen.add(current)
        tree.append(current)
        queue.extend(children.get(current, ()))
    return tree, resident


def tree_rss_mb(pid):
    """Resident MB of the whole tree under `pid`, and the pids it summed."""
    tree, resident = process_tree(pid)
    return sum(resident.get(one, 0) for one in tree) // 1024, tree


def kill_tree(pids):
    """SIGKILL every pid, children before parents.

    Parents last: killing the top of the tree first reparents whatever it
    had started to launchd, and an orphan holding the memory is the thing
    this is here to prevent.
    """
    for pid in reversed(pids):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # exited between the listing and the signal


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit-mb", type=int, default=1500)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given")

    child = subprocess.Popen(command)
    peak = 0
    killed = False
    while child.poll() is None:
        current, tree = tree_rss_mb(child.pid)
        peak = max(peak, current)
        if current > args.limit_mb:
            # the child's own pid first, so `kill_tree` signals it last
            kill_tree(list(dict.fromkeys([child.pid, *tree])))
            killed = True
            break
        time.sleep(0.2)
    child.wait()
    print(
        f"memcap: peak {peak} MB"
        + (f", killed past {args.limit_mb} MB" if killed else ""),
        file=sys.stderr,
    )
    sys.exit(137 if killed else child.returncode)


if __name__ == "__main__":
    main()
