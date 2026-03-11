"""Config sub-app — interactive GlobalConfig editor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Input, Label, Select, Static, Switch

from dimos.utils.cli import theme
from dimos.utils.cli.dui.sub_app import SubApp

_VIEWER_OPTIONS = ["rerun", "rerun-web", "rerun-connect", "foxglove", "none"]

_DEFAULTS: dict[str, object] = {
    "viewer": "rerun",
    "n_workers": 2,
    "robot_ip": "",
    "dtop": False,
}


def _config_path() -> Path:
    """Return the path to the persisted dio config file inside .venv."""
    # Walk up from the interpreter to find the venv root
    venv = Path(sys.prefix)
    return venv / "dio-config.json"


def _load_config() -> dict[str, object]:
    """Load saved config, falling back to defaults."""
    values = dict(_DEFAULTS)
    try:
        data = json.loads(_config_path().read_text())
        for k in _DEFAULTS:
            if k in data:
                values[k] = data[k]
    except Exception:
        pass
    return values


def _save_config(values: dict[str, object]) -> None:
    """Persist config values to disk."""
    try:
        _config_path().write_text(json.dumps(values, indent=2) + "\n")
    except Exception:
        pass


class ConfigSubApp(SubApp):
    TITLE = "config"

    DEFAULT_CSS = f"""
    ConfigSubApp {{
        layout: vertical;
        padding: 1 2;
        background: {theme.BACKGROUND};
        overflow-y: auto;
    }}
    ConfigSubApp .subapp-header {{
        color: #ff8800;
        padding: 0;
        text-style: bold;
    }}
    ConfigSubApp Label {{
        margin-top: 1;
        color: {theme.ACCENT};
    }}
    ConfigSubApp .field-label {{
        color: {theme.CYAN};
        margin-bottom: 0;
    }}
    ConfigSubApp Input {{
        width: 40;
    }}
    ConfigSubApp Select {{
        width: 40;
    }}
    ConfigSubApp .switch-row {{
        height: 3;
        margin-top: 1;
    }}
    ConfigSubApp .switch-row Label {{
        margin-top: 0;
        padding: 1 0;
    }}
    ConfigSubApp .switch-state {{
        color: {theme.DIM};
        padding: 1 1;
        width: 6;
    }}
    ConfigSubApp .switch-state.--on {{
        color: {theme.CYAN};
    }}
    """

    def __init__(self) -> None:
        super().__init__()
        self.config_values: dict[str, object] = _load_config()

    def compose(self) -> ComposeResult:
        v = self.config_values
        yield Static("GlobalConfig Editor", classes="subapp-header")

        yield Label("viewer", classes="field-label")
        yield Select(
            [(opt, opt) for opt in _VIEWER_OPTIONS],
            value=str(v.get("viewer", "rerun")),
            id="cfg-viewer",
        )

        yield Label("n_workers", classes="field-label")
        yield Input(value=str(v.get("n_workers", 2)), id="cfg-n-workers", type="integer")

        yield Label("robot_ip", classes="field-label")
        yield Input(value=str(v.get("robot_ip", "")), placeholder="e.g. 192.168.12.1", id="cfg-robot-ip")

        dtop_val = bool(v.get("dtop", False))
        with Horizontal(classes="switch-row"):
            yield Label("dtop", classes="field-label")
            yield Switch(value=dtop_val, id="cfg-dtop")
            state = Static("ON" if dtop_val else "OFF", id="cfg-dtop-state", classes="switch-state")
            if dtop_val:
                state.add_class("--on")
            yield state

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "cfg-viewer":
            self.config_values["viewer"] = event.value
            _save_config(self.config_values)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "cfg-n-workers":
            try:
                self.config_values["n_workers"] = int(event.value)
            except ValueError:
                pass
            _save_config(self.config_values)
        elif event.input.id == "cfg-robot-ip":
            self.config_values["robot_ip"] = event.value
            _save_config(self.config_values)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "cfg-dtop":
            self.config_values["dtop"] = event.value
            state_label = self.query_one("#cfg-dtop-state", Static)
            if event.value:
                state_label.update("ON")
                state_label.add_class("--on")
            else:
                state_label.update("OFF")
                state_label.remove_class("--on")
            _save_config(self.config_values)

    def get_overrides(self) -> dict[str, object]:
        """Return config overrides for use by the runner."""
        overrides: dict[str, object] = {}
        for k, v in self.config_values.items():
            if k == "robot_ip" and not v:
                continue
            overrides[k] = v
        return overrides
