"""LCM Spy sub-app — embedded LCM traffic monitor."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import DataTable

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp


class LCMSpySubApp(SubApp):
    TITLE = "lcmspy"

    DEFAULT_CSS = f"""
    LCMSpySubApp {{
        layout: vertical;
        background: {theme.BACKGROUND};
    }}
    LCMSpySubApp DataTable {{
        height: 1fr;
        width: 1fr;
        border: solid {theme.DIM};
        background: {theme.BG};
        scrollbar-size: 0 0;
    }}
    LCMSpySubApp DataTable > .datatable--header {{
        color: {theme.ACCENT};
        background: transparent;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self._spy: Any = None

    def compose(self) -> ComposeResult:
        table: DataTable = DataTable(zebra_stripes=False, cursor_type=None)  # type: ignore[arg-type]
        table.add_column("Topic")
        table.add_column("Freq (Hz)")
        table.add_column("Bandwidth")
        table.add_column("Total Traffic")
        yield table

    def on_mount_subapp(self) -> None:
        self.run_worker(self._init_lcm, exclusive=True, thread=True)
        self.set_interval(0.5, self._refresh_table)

    def _init_lcm(self) -> None:
        """Blocking LCM init — runs in a worker thread."""
        try:
            from dimos.utils.cli.lcmspy.lcmspy import GraphLCMSpy

            self._spy = GraphLCMSpy(autoconf=True, graph_log_window=0.5)
            self._spy.start()
        except Exception:
            pass

    def on_unmount_subapp(self) -> None:
        if self._spy:
            try:
                self._spy.stop()
            except Exception:
                pass
            self._spy = None

    def _refresh_table(self) -> None:
        if not self._spy:
            return

        from dimos.utils.cli.lcmspy.run_lcmspy import gradient, topic_text

        try:
            table = self.query_one(DataTable)
        except Exception:
            return
        topics = list(self._spy.topic.values())
        topics.sort(key=lambda t: t.total_traffic(), reverse=True)
        table.clear(columns=False)

        for t in topics:
            freq = t.freq(5.0)
            kbps = t.kbps(5.0)
            table.add_row(
                topic_text(t.name),
                Text(f"{freq:.1f}", style=gradient(10, freq)),
                Text(t.kbps_hr(5.0), style=gradient(1024 * 3, kbps)),
                Text(t.total_traffic_hr()),
            )
