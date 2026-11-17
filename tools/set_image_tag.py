#!/usr/bin/env python3
"""Set the CI-managed image tags in the agents chart values (REQ-CI-11, CI-12, CI-14).

    tools/set_image_tag.py VALUES_FILE SHA COMPONENT [COMPONENT ...]

Rewrites only lines of the form ``tag: <value> # ci-managed:<component>``. Every other
byte of the file is left as it was, so the commit shows exactly the tag change. Prints
``changed`` or ``unchanged`` and exits 0; exits 1 on an invalid SHA or a missing marker.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
TAG_LINE = re.compile(
    r"^(?P<head>\s*tag:\s*)(?P<value>\S+)(?P<tail>\s+#\s*ci-managed:(?P<component>[a-z0-9-]+)\s*)$"
)


def update(text: str, sha: str, components: list[str]) -> tuple[str, bool]:
    if not SHA.match(sha):
        raise ValueError(f"not a full lowercase commit SHA: {sha!r}")
    wanted = set(components)
    found: set[str] = set()
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        match = TAG_LINE.match(body)
        if match and match.group("component") in wanted:
            found.add(match.group("component"))
            body = f"{match.group('head')}{sha}{match.group('tail')}"
        out.append(body + ending)
    missing = wanted - found
    if missing:
        raise ValueError(f"no ci-managed tag line for: {', '.join(sorted(missing))}")
    new = "".join(out)
    return new, new != text


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__, file=sys.stderr)
        return 1
    path, sha, components = Path(argv[1]), argv[2], argv[3:]
    try:
        new, changed = update(path.read_text(), sha, components)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if changed:
        path.write_text(new)
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
