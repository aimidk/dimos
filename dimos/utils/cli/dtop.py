# Copyright 2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Live TUI for per-worker resource stats over LCM.

Usage:
    uv run python -m dimos.utils.cli.dtop [--topic /dimos/resource_stats]
"""

from __future__ import annotations

import threading
import time
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import VerticalScroll
from textual.widgets import Static

from dimos.protocol.pubsub.impl.lcmpubsub import PickleLCM, Topic
from dimos.utils.cli import theme


def _heat(ratio: float) -> str:
    """Map 0..1 ratio to a cyan → yellow → red gradient."""
    cyan = Color.parse(theme.CYAN)
    yellow = Color.parse(theme.YELLOW)
    red = Color.parse(theme.RED)
    if ratio <= 0.5:
        return cyan.blend(yellow, ratio * 2).hex
    return yellow.blend(red, (ratio - 0.5) * 2).hex


def _bar(value: float, max_val: float, width: int = 12) -> Text:
    """Render a tiny colored bar."""
    ratio = min(value / max_val, 1.0) if max_val > 0 else 0.0
    filled = int(ratio * width)
    return Text("█" * filled + "░" * (width - filled), style=_heat(ratio))


def _fmt_bytes(val: int) -> Text:
    mb = val / 1048576
    if mb >= 1024:
        return Text(f"{mb / 1024:.1f} GB", style=theme.BRIGHT_YELLOW)
    return Text(f"{mb:.1f} MB", style=theme.WHITE)


def _fmt_pct(val: float) -> Text:
    return Text(f"{val:.0f}%", style=_heat(min(val / 100.0, 1.0)))


def _fmt_time(seconds: float) -> Text:
    if seconds >= 3600:
        return Text(f"{seconds / 3600:.1f}h", style=theme.WHITE)
    if seconds >= 60:
        return Text(f"{seconds / 60:.1f}m", style=theme.WHITE)
    return Text(f"{seconds:.1f}s", style=theme.WHITE)


class ResourceSpyApp(App):  # type: ignore[type-arg]
    CSS_PATH = "dimos.tcss"

    TITLE = ""
    SHOW_TREE = False

    CSS = f"""
    Screen {{
        layout: vertical;
        background: {theme.BACKGROUND};
    }}
    VerticalScroll {{
        height: 1fr;
        scrollbar-size: 0 0;
    }}
    #panels {{
        background: transparent;
    }}
    """

    BINDINGS = [("q", "quit"), ("ctrl+c", "quit")]

    def __init__(self, topic_name: str = "/dimos/resource_stats") -> None:
        super().__init__()
        self._topic_name = topic_name
        self._lcm = PickleLCM(autoconf=True)
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._last_msg_time: float = 0.0

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(id="panels")

    def on_mount(self) -> None:
        self._lcm.subscribe(Topic(self._topic_name), self._on_msg)
        self._lcm.start()
        self.set_interval(1.0, self._refresh)

    async def on_unmount(self) -> None:
        self._lcm.stop()

    def _on_msg(self, msg: dict[str, Any], _topic: str) -> None:
        with self._lock:
            self._latest = msg
            self._last_msg_time = time.monotonic()

    def _refresh(self) -> None:
        with self._lock:
            data = self._latest
            last_msg = self._last_msg_time

        if data is None:
            return

        stale = (time.monotonic() - last_msg) > 2.0
        dim = "#606060"
        border_style = dim if stale else theme.BORDER

        # Collect (role, role_style, data_dict, modules) entries
        entries: list[tuple[str, str, dict[str, Any], str]] = []

        coord = data.get("coordinator", {})
        entries.append(("coordinator", theme.BRIGHT_CYAN, coord, ""))

        for w in data.get("workers", []):
            alive = w.get("alive", False)
            wid = w.get("worker_id", "?")
            role_style = theme.BRIGHT_GREEN if alive else theme.BRIGHT_RED
            modules = ", ".join(w.get("modules", [])) or ""
            entries.append((f"worker {wid}", role_style, w, modules))

        # Build inner content: sections separated by Rules
        parts: list[RenderableType] = []
        for i, (role, rs, d, mods) in enumerate(entries):
            if i > 0:
                # Titled divider between processes
                title = Text(
                    f" {role}: {mods} " if mods else f" {role} ", style=dim if stale else rs
                )
                parts.append(Rule(title=title, style=border_style))
            parts.extend(self._make_lines(d, stale))

        # First entry title goes on the Panel itself
        first_role, first_rs, _, first_mods = entries[0]
        panel_title = Text(
            f" {first_role}: {first_mods} " if first_mods else f" {first_role} ",
            style=dim if stale else first_rs,
        )

        panel = Panel(
            Group(*parts),
            title=panel_title,
            border_style=border_style,
        )
        self.query_one("#panels", Static).update(panel)

    @staticmethod
    def _make_lines(d: dict[str, Any], stale: bool) -> list[Text]:
        dim = "#606060"
        dim2 = "#505050"

        cpu = d.get("cpu_percent", 0)
        pss_text = _fmt_bytes(d.get("pss", 0))
        thr = d.get("num_threads", 0)
        ch = d.get("num_children", 0)
        fds = d.get("num_fds", 0)

        # Line 1: CPU% + bar + PSS + Thr/Child/FDs
        line1 = Text()
        line1.append("CPU ", style=dim if stale else theme.WHITE)
        line1.append(f"{cpu:.0f}%", style=dim if stale else _heat(min(cpu / 100.0, 1.0)))
        line1.append("  ")
        if stale:
            line1.append("░" * 12, style=dim)
        else:
            line1.append_text(_bar(cpu, 100))
        line1.append("  PSS ", style=dim if stale else theme.WHITE)
        line1.append(
            pss_text.plain,
            style=dim if stale else (pss_text.style or theme.WHITE),
        )
        line1.append(f"  Thr {thr}", style=dim if stale else theme.WHITE)
        line1.append(f"  Child {ch}", style=dim if stale else theme.WHITE)
        line1.append(f"  FDs {fds}", style=dim if stale else theme.WHITE)

        # Line 2: CPU times + IO R/W
        s2 = dim if stale else dim2
        user_t = _fmt_time(d.get("cpu_time_user", 0)).plain
        sys_t = _fmt_time(d.get("cpu_time_system", 0)).plain
        iow_t = _fmt_time(d.get("cpu_time_iowait", 0)).plain
        io_r = d.get("io_read_bytes", 0) / 1048576
        io_w = d.get("io_write_bytes", 0) / 1048576

        line2 = Text()
        line2.append(f"User {user_t}  Sys {sys_t}  IOw {iow_t}", style=s2)
        line2.append(f"  IO R/W {io_r:.0f}/{io_w:.0f} MB", style=s2)

        return [line1, line2]


_PREVIEW_DATA: dict[str, Any] = {
    "coordinator": {
        "cpu_percent": 12.3,
        "pss": 47_400_000,
        "num_threads": 4,
        "num_children": 0,
        "num_fds": 32,
        "cpu_time_user": 1.2,
        "cpu_time_system": 0.3,
        "cpu_time_iowait": 0.0,
        "io_read_bytes": 12_582_912,
        "io_write_bytes": 4_194_304,
        "pid": 1234,
    },
    "workers": [
        {
            "worker_id": 0,
            "alive": True,
            "modules": ["nav", "lidar"],
            "cpu_percent": 34.0,
            "pss": 125_829_120,
            "num_threads": 8,
            "num_children": 2,
            "num_fds": 64,
            "cpu_time_user": 5.1,
            "cpu_time_system": 1.0,
            "cpu_time_iowait": 0.2,
            "io_read_bytes": 47_185_920,
            "io_write_bytes": 12_582_912,
            "pid": 1235,
        },
        {
            "worker_id": 1,
            "alive": False,
            "modules": ["vision"],
            "cpu_percent": 87.0,
            "pss": 536_870_912,
            "num_threads": 16,
            "num_children": 1,
            "num_fds": 128,
            "cpu_time_user": 42.5,
            "cpu_time_system": 8.3,
            "cpu_time_iowait": 1.1,
            "io_read_bytes": 1_073_741_824,
            "io_write_bytes": 536_870_912,
            "pid": 1236,
        },
    ],
}


def _preview() -> None:
    """Print a static preview with fake data (no LCM needed)."""
    from rich.console import Console

    data = _PREVIEW_DATA
    border_style = theme.BORDER

    entries: list[tuple[str, str, dict[str, Any], str]] = []
    entries.append(("coordinator", theme.BRIGHT_CYAN, data["coordinator"], ""))
    for w in data["workers"]:
        rs = theme.BRIGHT_GREEN if w.get("alive") else theme.BRIGHT_RED
        mods = ", ".join(w.get("modules", []))
        entries.append((f"worker {w['worker_id']}", rs, w, mods))

    parts: list[RenderableType] = []
    for i, (role, rs, d, mods) in enumerate(entries):
        if i > 0:
            title = Text(f" {role}: {mods} " if mods else f" {role} ", style=rs)
            parts.append(Rule(title=title, style=border_style))
        parts.extend(ResourceSpyApp._make_lines(d, stale=False))

    first_role, first_rs, _, first_mods = entries[0]
    panel_title = Text(f" {first_role} ", style=first_rs)
    Console().print(Panel(Group(*parts), title=panel_title, border_style=border_style))


def main() -> None:
    import sys

    if "--preview" in sys.argv:
        _preview()
        return

    topic = "/dimos/resource_stats"
    if len(sys.argv) > 1 and sys.argv[1] == "--topic" and len(sys.argv) > 2:
        topic = sys.argv[2]

    ResourceSpyApp(topic_name=topic).run()


if __name__ == "__main__":
    main()
