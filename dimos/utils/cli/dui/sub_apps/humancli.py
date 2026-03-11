"""HumanCLI sub-app — embedded agent chat interface."""

from __future__ import annotations

import json
import textwrap
import threading
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp


class HumanCLISubApp(SubApp):
    TITLE = "chat"

    DEFAULT_CSS = f"""
    HumanCLISubApp {{
        layout: vertical;
        background: {theme.BACKGROUND};
    }}
    HumanCLISubApp #hcli-chat {{
        height: 1fr;
    }}
    HumanCLISubApp RichLog {{
        height: 1fr;
        scrollbar-size: 0 0;
        border: solid {theme.DIM};
    }}
    HumanCLISubApp Input {{
        dock: bottom;
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self._human_transport: Any = None
        self._agent_transport: Any = None
        self._running = False

    def compose(self) -> ComposeResult:
        with Container(id="hcli-chat"):
            yield RichLog(id="hcli-log", highlight=True, markup=True, wrap=False)
        yield Input(placeholder="Type a message...", id="hcli-input")

    def on_mount_subapp(self) -> None:
        self._running = True
        self.run_worker(self._init_transports, exclusive=True, thread=True)
        log = self.query_one("#hcli-log", RichLog)
        self._add_system_message(log, "Connected to DimOS Agent Interface")

    def _init_transports(self) -> None:
        """Blocking transport init — runs in a worker thread."""
        try:
            from dimos.core.transport import pLCMTransport

            self._human_transport = pLCMTransport("/human_input")
            self._agent_transport = pLCMTransport("/agent")
        except Exception:
            return

        if self._agent_transport:
            self._subscribe_to_agent()

    def on_unmount_subapp(self) -> None:
        self._running = False

    def _subscribe_to_agent(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        def receive_msg(msg: Any) -> None:
            if not self._running:
                return
            try:
                log = self.query_one("#hcli-log", RichLog)
            except Exception:
                return
            timestamp = datetime.now().strftime("%H:%M:%S")

            if isinstance(msg, SystemMessage):
                self.app.call_from_thread(
                    self._add_message, log, timestamp, "system", str(msg.content)[:1000], theme.YELLOW
                )
            elif isinstance(msg, AIMessage):
                content = msg.content or ""
                tool_calls = getattr(msg, "tool_calls", None) or msg.additional_kwargs.get(
                    "tool_calls", []
                )
                if content:
                    self.app.call_from_thread(
                        self._add_message, log, timestamp, "agent", content, theme.AGENT
                    )
                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get("name", "unknown")
                        args = tc.get("args", {})
                        info = f"▶ {name}({json.dumps(args, separators=(',', ':'))})"
                        self.app.call_from_thread(
                            self._add_message, log, timestamp, "tool", info, theme.TOOL
                        )
            elif isinstance(msg, ToolMessage):
                self.app.call_from_thread(
                    self._add_message, log, timestamp, "tool", str(msg.content), theme.TOOL_RESULT
                )
            elif isinstance(msg, HumanMessage):
                self.app.call_from_thread(
                    self._add_message, log, timestamp, "human", str(msg.content), theme.HUMAN
                )

        self._agent_transport.subscribe(receive_msg)

    def _add_message(
        self, log: RichLog, timestamp: str, sender: str, content: str, color: str
    ) -> None:
        content = content.strip() if content else ""
        prefix = f" [{theme.TIMESTAMP}]{timestamp}[/{theme.TIMESTAMP}] [{color}]{sender:>8}[/{color}] │ "
        indent = " " * 19 + "│ "
        width = max(log.size.width - 24, 40) if log.size else 60

        for i, line in enumerate(content.split("\n")):
            wrapped = textwrap.wrap(line, width=width) or [""]
            if i == 0:
                log.write(prefix + f"[{color}]{wrapped[0]}[/{color}]")
                for wl in wrapped[1:]:
                    log.write(indent + f"[{color}]{wl}[/{color}]")
            else:
                for wl in wrapped:
                    log.write(indent + f"[{color}]{wl}[/{color}]")

    def _add_system_message(self, log: RichLog, content: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._add_message(log, timestamp, "system", content, theme.YELLOW)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "hcli-input":
            return
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""

        if message.lower() in ("/exit", "/quit"):
            return
        if message.lower() == "/clear":
            self.query_one("#hcli-log", RichLog).clear()
            return

        if self._human_transport:
            self._human_transport.publish(message)
