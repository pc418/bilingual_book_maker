"""Run a command under a resident-memory cap, and report its peak.

    python tools/memcap.py [--limit-mb 1500] -- python make_book.py ...

macOS has no enforceable per-process memory limit (`ulimit -v` is a
no-op), so this polls the child's RSS and kills it past the cap. Exists
because a smoke matrix once ran eleven translation cells in parallel and
one unbounded list comprehension at write time took a 16 GB machine
down before anything could be seen. Exit status is the child's, or 137
when the cap was hit; the last line on stderr is the peak in MB either
way, so a smoke log records what a cell costs.
"""

import argparse
import subprocess
import sys
import time


def rss_mb(pid):
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return int(out or 0) // 1024


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
        current = rss_mb(child.pid)
        peak = max(peak, current)
        if current > args.limit_mb:
            child.kill()
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
