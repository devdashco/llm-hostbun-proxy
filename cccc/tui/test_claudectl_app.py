#!/usr/bin/env python3
"""Headless smoke test for claudectl_app (the Textual port).

Monkeypatches every network / keychain / ps / cmux function on the `core`
module with fakes (so NOTHING dials the router, the keychain, or scans
processes), then drives the app with `app.run_test()` and asserts the four tabs
render and the account rows populate.

Run:
  uv run --with textual python3 test_claudectl_app.py     # or plain python3 if textual is present
"""
try:
    import textual  # noqa: F401
except ImportError:
    import os as _os
    import shutil as _shutil
    import sys as _sys
    _uv = _shutil.which("uv")
    if _uv:
        _os.execvp(_uv, [_uv, "run", "--with", "textual", "python3", __file__, *_sys.argv[1:]])
    _sys.exit("test: need textual (install uv or pip install textual)")

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claudectl_tui as core          # noqa: E402
import claudectl_app as app_mod       # noqa: E402
from textual.widgets import DataTable, OptionList, TabbedContent  # noqa: E402


FAKE_ROWS = [
    {"name": "philip", "local": True, "active": True, "owner": "philip@devdash.co",
     "org": "org-aaaa1111", "sub": "?", "status": "", "u5": 12.0, "r5": "3h 20m",
     "c5": "18:00", "u7": 44.0, "r7": "2d 4h", "c7": "Sat 12:00", "d7": "Sat 12 Jul",
     "wk_left": 0.5, "_reset7": 1_800_000_000, "machines": ["pmac"]},
    {"name": "william", "local": False, "active": False, "owner": "william@devdash.co",
     "org": "org-bbbb2222", "sub": "?", "status": "", "u5": None, "r5": "—",
     "c5": "", "u7": 91.0, "r7": "6h", "c7": "today 20:00", "d7": "Fri 11 Jul",
     "wk_left": 0.1, "_reset7": 1_800_100_000, "machines": []},
]


def _install_fakes(monkey):
    """Replace every side-effecting core function with an in-memory fake."""
    monkey["fetch"] = core.fetch
    core.fetch = lambda: {"rows": [dict(r) for r in FAKE_ROWS], "err": ""}
    core._llm_json = lambda sub, timeout=8: {}
    core._llm_post = lambda sub, body=None, timeout=30: {"ok": True}
    core._kc_read = lambda: {}
    core._kc_write = lambda blob: True
    core._claude_surfaces = lambda: []
    core._claude_windows = lambda: []
    core._mcp_get = lambda path: {}
    core._live_refresh = lambda: None
    core._version_check = lambda autosync=True: {"sha": "test", "state": "latest"}
    core._doctor_check = lambda: {"ok": True, "n": 0, "first": ""}
    core.gateway_set_lock = lambda account, consumer="": {"ok": True}


async def _run() -> None:
    _install_fakes({})
    # start_background=False → no live/version/doctor daemons dial out during the test
    app = app_mod.ClaudectlApp(start_background=False)
    async with app.run_test() as pilot:
        # let the fetch worker land + call_from_thread(_apply_data) drain
        await app.workers.wait_for_complete()
        await pilot.pause()

        # 1) all four tabs exist
        tc = app.query_one("#tabs", TabbedContent)
        ids = {p.id for p in tc.query("TabPane")}
        assert {"accounts", "windows", "plugins", "setup"} <= ids, f"missing tabs: {ids}"
        print(f"  ✓ tabs render: {sorted(ids)}")

        # 2) account rows populated from the fake fetch
        table = app.query_one("#accounts_table", DataTable)
        assert table.row_count == len(FAKE_ROWS), f"row_count={table.row_count}"
        first = table.get_row_at(0)
        assert any("philip" in str(c) for c in first), f"first row missing name: {first}"
        print(f"  ✓ account rows populate: {table.row_count} rows, first={str(first[1])!r}")

        # 3) pinned line reflects the local=True fake account
        pinned = str(app.query_one("#accounts_pinned").render())
        assert "philip" in pinned, f"pinned line: {pinned!r}"
        print(f"  ✓ pinned line: {pinned.strip()!r}")

        # 4) Windows tab action list carries every fleet action
        wl = app.query_one("#windows_actions", OptionList)
        assert wl.option_count == len(core._WINDOWS_ITEMS), wl.option_count
        print(f"  ✓ windows actions: {wl.option_count} options")

        # 5) tab switching via ←/→ bindings works (arrow nav)
        app.action_next_tab()
        await pilot.pause()
        assert tc.active == "windows", f"active={tc.active}"
        print(f"  ✓ ←/→ tab switch: now on {tc.active!r}")


def main() -> int:
    try:
        asyncio.run(_run())
    except AssertionError as e:
        print(f"FAIL: {e}")
        return 1
    print("PASS: claudectl_app smoke test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
