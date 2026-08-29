#!/usr/bin/env python3
"""Screenshot the dashboard headlessly, so layout can be eyeballed before delivery.

The palette validator checks colour, not geometry. This catches the things it
cannot: labels colliding near a chart axis, a table overflowing, a tile clipped.
"""

from __future__ import annotations

import argparse
import os
import sys

from playwright.sync_api import sync_playwright


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--html", default="out/sla-dashboard.html")
    p.add_argument("--out", default="out/shot")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--scheme", default="light", choices=["light", "dark"])
    p.add_argument("--full", action="store_true", help="Full page, not just the fold")
    p.add_argument("--height", type=int, default=1400)
    args = p.parse_args()

    path = os.path.abspath(args.html)
    if not os.path.exists(path):
        print(f"No such file: {path}", file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    # Use the preinstalled Chromium rather than letting Playwright fetch its own,
    # which the pinned pip version would otherwise insist on.
    exe = os.environ.get("CHROMIUM_PATH", "/opt/pw-browsers/chromium")
    launch: dict = {"executable_path": exe} if os.path.exists(exe) else {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch)
        page = browser.new_page(
            viewport={"width": args.width, "height": args.height},
            color_scheme=args.scheme,
            device_scale_factor=2,
        )
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.url}"))

        page.goto(f"file://{path}", wait_until="load")
        page.wait_for_timeout(300)

        # A self-contained page must not have reached out for anything.
        external = page.evaluate(
            "() => performance.getEntriesByType('resource')"
            ".map(r => r.name).filter(n => !n.startsWith('file:'))"
        )
        if external:
            errors.append(f"external requests: {external}")

        out = f"{args.out}-{args.scheme}.png"
        page.screenshot(path=out, full_page=args.full)
        browser.close()

    print(f"Wrote {out}")
    for err in errors:
        print(f"  PROBLEM: {err}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
