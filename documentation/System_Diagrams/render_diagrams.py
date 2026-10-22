#!/usr/bin/env python3
"""Render the Mermaid diagram sources to PNG for embedding in the documents.

The ``.mmd`` files in this folder are the editable sources; the ``.png`` files
are the rendered output that the Word and Markdown documents embed. Re-running
this script regenerates the pictures after an edit to a source file.

Two rendering settings matter for the printed result:

* ``-w`` is only the *viewport* the headless browser is given. Mermaid scales
  the diagram to fit that viewport, so a viewport narrower than the diagram
  shrinks the type. The viewport is therefore set very wide (12000 px) and the
  diagram is rendered at its natural size; the PNG is then rasterised at 2x so
  that it stays sharp when scaled into the page.
* Each source carries a ``config:`` block in its YAML front matter that sets
  the diagram font size and tightens the node padding and spacing. Those
  settings determine how large the type is *relative to the canvas*, which is
  what decides whether the figure is legible at the size it is printed.

Requires Node.js and the Mermaid CLI. The renderer is invoked through
``npx @mermaid-js/mermaid-cli``; a headless Chrome shell must be available to
Puppeteer. Set ``PUPPETEER_EXECUTABLE_PATH`` if it is not on the default path.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent

MERMAID_CLI = "@mermaid-js/mermaid-cli@11"

#: Wide viewport so Mermaid renders the diagram at its natural size instead of
#: scaling it down to fit the page.
VIEWPORT_WIDTH = 12000

#: Rasterisation factor for the PNG.
SCALE = 2


def chrome_path() -> str | None:
    """Find a headless Chrome shell for Puppeteer, if one is installed."""
    env = os.environ.get("PUPPETEER_EXECUTABLE_PATH")
    if env and Path(env).is_file():
        return env
    cache = Path.home() / ".cache/puppeteer/chrome-headless-shell"
    if cache.is_dir():
        for candidate in cache.rglob("chrome-headless-shell"):
            if candidate.is_file():
                return str(candidate)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


def render(source: Path, target: Path) -> bool:
    chrome = chrome_path()
    env = dict(os.environ)
    if chrome:
        env["PUPPETEER_EXECUTABLE_PATH"] = chrome

    command = [
        "npx",
        "-y",
        MERMAID_CLI,
        "-i",
        str(source),
        "-o",
        str(target),
        "-b",
        "white",
        "-w",
        str(VIEWPORT_WIDTH),
        "-s",
        str(SCALE),
    ]
    try:
        result = subprocess.run(
            command, cwd=str(source.parent), env=env, capture_output=True, text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  FAILED    {target.name}: {exc}")
        return False

    if result.returncode != 0 or not target.is_file():
        print(f"  FAILED    {target.name}")
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-6:]
        for line in tail:
            print(f"      {line}")
        return False

    print(f"  rendered  {target.name}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(HERE),
        help="Directory the PNG files are written to (default: alongside the sources).",
    )
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sources = sorted(HERE.glob("*.mmd"))
    if not sources:
        print("No .mmd sources found in", HERE)
        return 1

    ok = 0
    for source in sources:
        target = out_dir / f"{source.stem}.png"
        if render(source, target):
            ok += 1

    print(f"\n{ok} of {len(sources)} diagrams rendered into {out_dir}")
    return 0 if ok == len(sources) else 1


if __name__ == "__main__":
    sys.exit(main())
