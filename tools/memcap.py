"""Run a command under a resident-memory cap, and report its peak.

    python tools/memcap.py [--limit-mb 1500] -- python make_book.py ...

macOS has no enforceable per-process memory limit (`ulimit -v` is a
no-op), so this polls the RSS of the command *and everything it started*
and kills the lot past the cap. Exists because a smoke matrix once ran
eleven translation cells in parallel and one unbounded list comprehension
at write time took a 16 GB machine down before anything could be seen.
Exit status is the child's, or 137 when the cap was hit; the last lines on
stderr are the peak in MB and anything that had to be cleaned up, so a
smoke log records what a cell costs.

The unit is a process group, not a pid list. The command runs in a
session of its own (`start_new_session`), so it leads a group that every
process it starts inherits — the codex route's `codex app-server` sidecar
(`book_maker/codex_client.py`) included, whose memory does not appear in
its parent's RSS. `ps` then answers the whole group's RSS in one read,
and `os.killpg` kills it in one call: no pid list that can go stale
between the listing and the signal, and nothing reparented to launchd
still holding the memory. The group also outlives the command, which is
the point of the survivor sweep below: a sidecar the cell forgot to reap
is still in the group after the cell is gone.
"""

import argparse
import os
import signal
import subprocess
import sys
import time


def group_rss_mb(pgid):
    """Resident MB of the whole process group, and the pids that make it up."""
    listing = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,rss="], capture_output=True, text=True
    ).stdout
    kilobytes, members = 0, []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 3:
            continue
        try:
            pid, group, resident = (int(field) for field in fields)
        except ValueError:
            continue
        if group == pgid:
            kilobytes += resident
            members.append(pid)
    return kilobytes // 1024, members


def kill_group(pgid):
    """SIGKILL the process group.

    One call the kernel resolves itself, so there is no window in which a
    listed pid has exited and been recycled onto something unrelated.
    """
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # the group is already gone


def _die_on_sigterm(signum, _frame):
    # SIGTERM's default action would end memcap without unwinding, leaving
    # the group behind; raising turns it into the same path as a Ctrl-C.
    raise SystemExit(128 + signum)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit-mb", type=int, default=1500)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("no command given")

    signal.signal(signal.SIGTERM, _die_on_sigterm)

    # start_new_session makes the child a session and process-group leader,
    # so its pgid is its pid and everything it spawns joins that group.
    child = subprocess.Popen(command, start_new_session=True)
    group = child.pid
    peak = 0
    killed = False
    survivors = 0
    try:
        while child.poll() is None:
            current, _members = group_rss_mb(group)
            peak = max(peak, current)
            if current > args.limit_mb:
                kill_group(group)
                killed = True
                break
            time.sleep(0.2)
        child.wait()
        if not killed:
            # The command is gone. Anything still in its group it left
            # behind, holding memory nothing is watching any more.
            current, members = group_rss_mb(group)
            peak = max(peak, current)
            survivors = len(members)
            if survivors:
                kill_group(group)
    except BaseException:
        # a Ctrl-C, a SIGTERM, or a bug in here: none of them may leave
        # the cell and its sidecars running
        kill_group(group)
        raise

    print(
        f"memcap: peak {peak} MB"
        + (f", killed past {args.limit_mb} MB" if killed else ""),
        file=sys.stderr,
    )
    if survivors:
        print(f"memcap: killed {survivors} survivor(s) after exit", file=sys.stderr)
    # the cell's own status still decides; the survivor line is the loud part
    sys.exit(137 if killed else child.returncode)


if __name__ == "__main__":
    main()
