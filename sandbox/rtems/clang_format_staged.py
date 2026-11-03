#!/usr/bin/env python3
"""Format only added/changed C and C++ lines in the staged diff."""

import re
import subprocess
import sys


diff = subprocess.run(
    ["git", "diff", "--cached", "--unified=0", "--", "*.c", "*.h", "*.cc", "*.cpp", "*.hpp"],
    check=True,
    capture_output=True,
    text=True,
).stdout

changed = {}
current = None
for line in diff.splitlines():
    if line.startswith("+++ b/"):
        current = line[6:]
        changed.setdefault(current, [])
    elif line.startswith("@@") and current:
        match = re.search(r"\+(\d+)(?:,(\d+))?", line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            if count:
                changed[current].append((start, start + count - 1))

for path, ranges in changed.items():
    if not ranges:
        continue
    args = ["clang-format", "-i"]
    for start, end in ranges:
        args.extend([f"--lines={start}:{end}"])
    args.append(path)
    subprocess.run(args, check=True)

sys.exit(0)
