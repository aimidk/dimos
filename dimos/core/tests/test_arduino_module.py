# Copyright 2025-2026 Dimensional Inc.
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

"""Unit tests for dimos.core.arduino_module.

Covers the pure/host-side logic — header generation, topic enum
assignment, the three-way registry sync, port detection with mocked
arduino-cli, and QEMU cleanup paths.  These tests do not require a real
Arduino or QEMU.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from typing import Any
from unittest import mock

import pytest

from dimos.core.arduino_module import (
    _ARDUINO_HW_DIR,
    _KNOWN_TYPE_HEADERS,
    ArduinoModule,
    ArduinoModuleConfig,
)
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.Twist import Twist

# Fixtures / helpers


class _ExampleConfig(ArduinoModuleConfig):
    """Minimal config for tests — no auto-detect, no flash, no virtual."""

    sketch_path: str = "sketch/sketch.ino"
    board_fqbn: str = "arduino:avr:uno"
    baudrate: int = 115200
    auto_detect: bool = False
    auto_flash: bool = False
    virtual: bool = False
    port: str | None = "/dev/ttyACM0"
    # Custom config field that should end up in the generated header.
    greeting: str = 'he said "hi"'
    tick_rate_hz: int = 50


class _ExampleModule(ArduinoModule):
    config: _ExampleConfig
    twist_in: In[Twist]
    twist_echo_out: Out[Twist]


def _make_module() -> _ExampleModule:
    """Build an _ExampleModule without triggering its __init__ machinery.

    ArduinoModule subclasses pydantic Module whose real `__init__` spins
    up RPC / worker plumbing we don't need for unit tests.  We use
    `__new__` to bypass it, then install bare `In` / `Out` stubs (via
    `__new__` again) into `self.__dict__` — the `Module.inputs` /
    `Module.outputs` properties are read-only `@property`s that scan
    `__dict__` for any attribute that is an `In`/`Out` instance, so
    this is the minimum required to make those properties return the
    expected port names.
    """
    inst = _ExampleModule.__new__(_ExampleModule)
    inst.config = _ExampleConfig()
    inst.__dict__["twist_in"] = In.__new__(In)
    inst.__dict__["twist_echo_out"] = Out.__new__(Out)
    return inst


# _build_topic_enum


def test_build_topic_enum_assigns_1_based_alphabetical() -> None:
    mod = _make_module()
    enum = mod._build_topic_enum()
    # Alphabetical order, topic 0 reserved for debug.
    assert enum == {"twist_echo_out": 1, "twist_in": 2}


# _generate_header — config embedding & escaping


def test_generate_header_escapes_quoted_strings(tmp_path: Path) -> None:
    """A config string containing " or \\ must not produce invalid C."""
    mod = _make_module()
    # Patch _resolve_sketch_dir so the generated header lands in tmp.
    with mock.patch.object(mod, "_build_dir", return_value=tmp_path):
        mod._generate_header()
    text = (tmp_path / "dimos_arduino.h").read_text()

    # The greeting contains an embedded double-quote.  If the header
    # generator naively interpolated it, the resulting C file would have
    # an unterminated string literal.  `json.dumps` escapes it to \".
    assert r'#define DIMOS_GREETING "he said \"hi\""' in text
    assert "#define DIMOS_BAUDRATE 115200" in text
    assert "#define DIMOS_TICK_RATE_HZ 50" in text


def test_generate_header_includes_topic_enum_and_message_header(
    tmp_path: Path,
) -> None:
    mod = _make_module()
    with mock.patch.object(mod, "_build_dir", return_value=tmp_path):
        mod._generate_header()
    text = (tmp_path / "dimos_arduino.h").read_text()

    assert "enum dimos_topic {" in text
    assert "DIMOS_TOPIC_DEBUG = 0" in text
    assert "DIMOS_TOPIC__TWIST_ECHO_OUT = 1" in text
    assert "DIMOS_TOPIC__TWIST_IN = 2" in text
    # Twist → geometry_msgs/Twist.h
    assert '#include "geometry_msgs/Twist.h"' in text
    # DSP core always pulled in.
    assert '#include "dsp_protocol.h"' in text


def test_generate_header_rejects_non_finite_float(tmp_path: Path) -> None:
    mod = _make_module()
    mod.config.reconnect_interval = float("inf")

    # reconnect_interval is in arduino_config_exclude, so it won't even
    # be considered.  Use a field that IS embeddable instead — add one
    # via direct mutation of an allowed numeric field.  Using baudrate
    # (int) won't work because it's int.  We patch config.__class__
    # model_fields with a synthetic non-finite field via a subclass.
    class _NaNConfig(_ExampleConfig):
        nan_val: float = float("nan")

    mod.config = _NaNConfig()
    with mock.patch.object(mod, "_build_dir", return_value=tmp_path):
        with pytest.raises(ValueError, match="non-finite"):
            mod._generate_header()


def test_generate_header_rejects_unembeddable_type(tmp_path: Path) -> None:
    class _ListConfig(_ExampleConfig):
        the_list: list[int] = [1, 2, 3]

    mod = _make_module()
    mod.config = _ListConfig()
    with mock.patch.object(mod, "_build_dir", return_value=tmp_path):
        with pytest.raises(TypeError, match="Cannot embed config field 'the_list'"):
            mod._generate_header()


# _detect_port — mocked arduino-cli


def _run_result(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["arduino-cli"], returncode=returncode, stdout=stdout, stderr=""
    )


def test_detect_port_matches_fqbn() -> None:
    mod = _make_module()
    payload: dict[str, Any] = {
        "detected_ports": [
            {
                "port": {"address": "/dev/ttyACM1"},
                "matching_boards": [{"fqbn": "arduino:avr:uno"}],
            },
            {
                "port": {"address": "/dev/ttyUSB0"},
                "matching_boards": [{"fqbn": "arduino:avr:mega"}],
            },
        ]
    }
    with mock.patch(
        "dimos.core.arduino_module.subprocess.run",
        return_value=_run_result(json.dumps(payload)),
    ):
        assert mod._detect_port() == "/dev/ttyACM1"


def test_detect_port_raises_on_no_match() -> None:
    mod = _make_module()
    payload = {"detected_ports": []}
    with mock.patch(
        "dimos.core.arduino_module.subprocess.run",
        return_value=_run_result(json.dumps(payload)),
    ):
        with pytest.raises(RuntimeError, match="No Arduino board found matching FQBN"):
            mod._detect_port()


def test_detect_port_wraps_invalid_json() -> None:
    mod = _make_module()
    with mock.patch(
        "dimos.core.arduino_module.subprocess.run",
        return_value=_run_result("not-json-at-all"),
    ):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            mod._detect_port()


def test_detect_port_wraps_missing_arduino_cli() -> None:
    mod = _make_module()
    with mock.patch(
        "dimos.core.arduino_module.subprocess.run",
        side_effect=FileNotFoundError,
    ):
        with pytest.raises(RuntimeError, match="arduino-cli not found"):
            mod._detect_port()


def test_detect_port_wraps_non_zero_exit() -> None:
    mod = _make_module()
    with mock.patch(
        "dimos.core.arduino_module.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied"
        ),
    ):
        with pytest.raises(RuntimeError, match="permission denied"):
            mod._detect_port()


# _cleanup_qemu — idempotency + leak sealing


def test_cleanup_qemu_is_idempotent_on_unstarted_module() -> None:
    mod = _make_module()
    # Never started — all slots None.  Must not raise.
    mod._cleanup_qemu()
    mod._cleanup_qemu()
    assert mod._qemu_proc is None
    assert mod._qemu_log_fd is None
    assert mod._qemu_log_path is None
    assert mod._virtual_pty is None


def test_cleanup_qemu_closes_log_fd_and_removes_log_file(tmp_path: Path) -> None:
    mod = _make_module()
    log_path = tmp_path / "qemu.log"
    log_path.write_text("hi")
    mod._qemu_log_path = str(log_path)
    fd = open(log_path, "wb")
    mod._qemu_log_fd = fd
    mod._qemu_proc = None  # no process — just the fd + file

    mod._cleanup_qemu()

    assert fd.closed
    assert not log_path.exists()
    assert mod._qemu_log_fd is None
    assert mod._qemu_log_path is None


def test_cleanup_qemu_terminates_live_process() -> None:
    mod = _make_module()
    proc = mock.Mock(spec=subprocess.Popen)
    # poll() returns None while alive, then 0 after wait.
    proc.poll.side_effect = [None]
    proc.wait.return_value = 0
    mod._qemu_proc = proc

    mod._cleanup_qemu()

    proc.terminate.assert_called_once()
    proc.wait.assert_called_once_with(timeout=5)
    assert mod._qemu_proc is None


def test_cleanup_qemu_kills_on_terminate_timeout() -> None:
    mod = _make_module()
    proc = mock.Mock(spec=subprocess.Popen)
    proc.poll.side_effect = [None]
    proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="qemu", timeout=5), 0]
    mod._qemu_proc = proc

    mod._cleanup_qemu()

    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
    assert proc.wait.call_count == 2
    assert mod._qemu_proc is None


# Registry sync — _KNOWN_TYPE_HEADERS vs arduino_msgs/ vs main.cpp


def _arduino_common_dir() -> Path:
    return _ARDUINO_HW_DIR / "common" / "arduino_msgs"


def _main_cpp_path() -> Path:
    return _ARDUINO_HW_DIR / "cpp" / "main.cpp"


def test_registry_headers_exist_on_disk() -> None:
    common = _arduino_common_dir()
    missing = [
        (msg_name, header)
        for msg_name, header in _KNOWN_TYPE_HEADERS.items()
        if not (common / header).is_file()
    ]
    assert not missing, (
        f"Every entry in _KNOWN_TYPE_HEADERS must point to an existing "
        f"arduino_msgs header, but these are missing: {missing}"
    )


def test_registry_matches_main_cpp_hash_registry() -> None:
    """Every type in `_KNOWN_TYPE_HEADERS` must also appear in the C++
    bridge's `init_hash_registry()` and vice versa.  Either half is a
    silent wire-format bug waiting to happen."""
    main_cpp = _main_cpp_path().read_text()

    # The C++ side stores keys as "std_msgs.Time" etc.
    cpp_entries = set(re.findall(r'hash_registry\["([^"]+)"\]', main_cpp))
    py_entries = set(_KNOWN_TYPE_HEADERS.keys())

    only_in_py = py_entries - cpp_entries
    only_in_cpp = cpp_entries - py_entries

    assert not only_in_py, (
        f"These message types are in _KNOWN_TYPE_HEADERS but NOT in "
        f"main.cpp::init_hash_registry: {sorted(only_in_py)}. Add them to "
        f"dimos/hardware/arduino/cpp/main.cpp or remove from the Python registry."
    )
    assert not only_in_cpp, (
        f"These message types are in main.cpp::init_hash_registry but NOT "
        f"in _KNOWN_TYPE_HEADERS: {sorted(only_in_cpp)}. Add them to "
        f"dimos/core/arduino_module.py::_KNOWN_TYPE_HEADERS or remove from main.cpp."
    )


def test_registry_headers_cover_all_arduino_msgs_files() -> None:
    """Every .h under arduino_msgs/ must be referenced by the Python
    registry.  Orphan headers are dead code that still has to be
    maintained."""
    common = _arduino_common_dir()
    on_disk = {str(p.relative_to(common)) for p in common.rglob("*.h")}
    referenced = set(_KNOWN_TYPE_HEADERS.values())
    orphans = on_disk - referenced
    assert not orphans, (
        f"These arduino_msgs headers are not referenced by _KNOWN_TYPE_HEADERS "
        f"(dead code or missing registry entry): {sorted(orphans)}"
    )
