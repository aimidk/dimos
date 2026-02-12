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

"""NativeModule: blueprint-integrated wrapper for native (C/C++) executables.

A NativeModule is a thin Python Module subclass that declares In/Out ports
for blueprint wiring but delegates all real work to a managed subprocess.
The native process receives its LCM topic names via CLI args and does
pub/sub directly on the LCM multicast bus.

Example usage::

    @dataclass(kw_only=True)
    class MyConfig(NativeModuleConfig):
        executable: str = "./build/my_module"
        some_param: float = 1.0

    class MyCppModule(NativeModule):
        default_config = MyConfig
        pointcloud: Out[PointCloud2]
        cmd_vel: In[Twist]

    # Works with autoconnect, remappings, etc.
    autoconnect(
        MyCppModule.blueprint(),
        SomeConsumer.blueprint(),
    ).build().loop()
"""

from __future__ import annotations

from dataclasses import dataclass, field
import enum
import json
import os
import signal
import subprocess
import threading
from typing import IO

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.utils.logging_config import setup_logger

logger = setup_logger()


class LogFormat(enum.Enum):
    TEXT = "text"
    JSON = "json"


@dataclass(kw_only=True)
class NativeModuleConfig(ModuleConfig):
    """Configuration for a native (C/C++) subprocess module."""

    executable: str
    extra_args: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    shutdown_timeout: float = 10.0
    log_format: LogFormat = LogFormat.TEXT


class NativeModule(Module):
    """Module that wraps a native executable as a managed subprocess.

    Subclass this, declare In/Out ports, and set ``default_config`` to a
    :class:`NativeModuleConfig` subclass pointing at the executable.

    On ``start()``, the binary is launched with CLI args::

        <executable> --<port_name> <lcm_topic_string> ... <extra_args>

    The native process should parse these args and pub/sub on the given
    LCM topics directly.  On ``stop()``, the process receives SIGTERM.
    """

    default_config: type[NativeModuleConfig] = NativeModuleConfig  # type: ignore[assignment]
    _process: subprocess.Popen[bytes] | None = None
    _io_threads: list[threading.Thread]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._io_threads = []

    @rpc
    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            logger.warning("Native process already running", pid=self._process.pid)
            return

        topics = self._collect_topics()
        extra = self._build_extra_args()

        cmd = [self.config.executable]
        for name, topic_str in topics.items():
            cmd.extend([f"--{name}", topic_str])
        cmd.extend(extra)
        cmd.extend(self.config.extra_args)

        env = {**os.environ, **self.config.extra_env}
        cwd = self.config.cwd

        logger.info("Starting native process", cmd=" ".join(cmd), cwd=cwd)
        self._process = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logger.info("Native process started", pid=self._process.pid)

        self._io_threads = [
            self._start_reader(self._process.stdout, "info"),
            self._start_reader(self._process.stderr, "warning"),
        ]

    @rpc
    def stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            logger.info("Stopping native process", pid=self._process.pid)
            self._process.send_signal(signal.SIGTERM)
            try:
                self._process.wait(timeout=self.config.shutdown_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Native process did not exit, sending SIGKILL", pid=self._process.pid
                )
                self._process.kill()
                self._process.wait(timeout=5)
        for t in self._io_threads:
            t.join(timeout=2)
        self._io_threads = []
        self._process = None
        super().stop()

    def _start_reader(self, stream: IO[bytes] | None, level: str) -> threading.Thread:
        """Spawn a daemon thread that pipes a subprocess stream through the logger."""
        t = threading.Thread(target=self._read_log_stream, args=(stream, level), daemon=True)
        t.start()
        return t

    def _read_log_stream(self, stream: IO[bytes] | None, level: str) -> None:
        if stream is None:
            return
        log_fn = getattr(logger, level)
        for raw in stream:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            if self.config.log_format == LogFormat.JSON:
                try:
                    data = json.loads(line)
                    event = data.pop("event", line)
                    log_fn(event, **data)
                    continue
                except (json.JSONDecodeError, TypeError):
                    # TODO: log a warning about malformed JSON and the line contents
                    pass
            log_fn(line, pid=self._process.pid if self._process else None)
        stream.close()

    def _collect_topics(self) -> dict[str, str]:
        """Extract LCM topic strings from blueprint-assigned stream transports."""
        topics: dict[str, str] = {}
        for name in list(self.inputs) + list(self.outputs):
            stream = getattr(self, name, None)
            if stream is None:
                continue
            transport = getattr(stream, "_transport", None)
            if transport is None:
                continue
            topic = getattr(transport, "topic", None)
            if topic is not None:
                topics[name] = str(topic)
        return topics

    def _build_extra_args(self) -> list[str]:
        """Override in subclasses to pass additional config as CLI args.

        Called after topic args, before ``config.extra_args``.
        """
        return []


__all__ = [
    "NativeModule",
    "NativeModuleConfig",
]
