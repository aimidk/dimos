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

"""Tests for RerunWebSocketServer.

Uses ``MockViewerPublisher`` to simulate dimos-viewer sending events, matching
the exact JSON protocol used by the Rust ``WsPublisher`` in the viewer.
"""

import asyncio
import json
import threading
import time
from typing import Any

import pytest
import websockets.asyncio.client as ws_client

from dimos.visualization.rerun.websocket_server import RerunWebSocketServer

_TEST_PORT = 13031


class MockViewerPublisher:
    """Python mirror of the Rust WsPublisher in dimos-viewer.

    Connects to a running ``RerunWebSocketServer`` and exposes the same
    ``send_click`` / ``send_twist`` / ``send_stop`` / ``send_heartbeat``
    API that the real viewer uses.  Useful for unit tests that need to
    exercise the server without a real viewer binary.

    Usage::

        with MockViewerPublisher("ws://127.0.0.1:13031/ws") as pub:
            pub.send_click(1.0, 2.0, 0.0, "/world", timestamp_ms=1000)
            pub.send_twist(0.5, 0.0, 0.0, 0.0, 0.0, 0.8)
            pub.send_stop()
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def __enter__(self) -> "MockViewerPublisher":
        self._loop = asyncio.new_event_loop()
        self._ws = self._loop.run_until_complete(self._connect())
        return self

    def __exit__(self, *_: Any) -> None:
        if self._ws is not None and self._loop is not None:
            self._loop.run_until_complete(self._ws.close())
        if self._loop is not None:
            self._loop.close()

    async def _connect(self) -> Any:
        return await ws_client.connect(self._url)

    def send_click(
        self,
        x: float,
        y: float,
        z: float,
        entity_path: str = "",
        timestamp_ms: int = 0,
    ) -> None:
        """Send a click event — matches viewer SelectionChange handler output."""
        self._send(
            {
                "type": "click",
                "x": x,
                "y": y,
                "z": z,
                "entity_path": entity_path,
                "timestamp_ms": timestamp_ms,
            }
        )

    def send_twist(
        self,
        linear_x: float,
        linear_y: float,
        linear_z: float,
        angular_x: float,
        angular_y: float,
        angular_z: float,
    ) -> None:
        """Send a twist (WASD keyboard) event."""
        self._send(
            {
                "type": "twist",
                "linear_x": linear_x,
                "linear_y": linear_y,
                "linear_z": linear_z,
                "angular_x": angular_x,
                "angular_y": angular_y,
                "angular_z": angular_z,
            }
        )

    def send_stop(self) -> None:
        """Send a stop event (Space bar or key release)."""
        self._send({"type": "stop"})

    def send_heartbeat(self, timestamp_ms: int = 0) -> None:
        """Send a heartbeat (1 Hz keepalive from viewer)."""
        self._send({"type": "heartbeat", "timestamp_ms": timestamp_ms})

    def flush(self, delay: float = 0.1) -> None:
        """Wait briefly so the server processes queued messages."""
        time.sleep(delay)

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._loop is not None and self._ws is not None, "Not connected"
        self._loop.run_until_complete(self._ws.send(json.dumps(msg)))


def _collect(received: list[Any], done: threading.Event) -> Any:
    """Return a callback that appends to *received* and signals *done*."""

    def _cb(msg: Any) -> None:
        received.append(msg)
        done.set()

    return _cb


def _make_module(port: int = _TEST_PORT, cmd_vel_scaling: Any = None) -> RerunWebSocketServer:
    kwargs: dict[str, Any] = {"port": port}
    if cmd_vel_scaling is not None:
        kwargs["cmd_vel_scaling"] = cmd_vel_scaling
    return RerunWebSocketServer(**kwargs)


def _wait_for_server(port: int, timeout: float = 3.0) -> None:
    """Block until the WebSocket server accepts an upgrade handshake."""

    async def _probe() -> None:
        async with ws_client.connect(f"ws://127.0.0.1:{port}/ws"):
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            asyncio.run(_probe())
            return
        except Exception:
            time.sleep(0.05)
    raise TimeoutError(f"Server on port {port} did not become ready within {timeout}s")


class TestRerunWebSocketServerStartup:
    def test_server_binds_port(self) -> None:
        """After start(), the server must be reachable on the configured port."""
        mod = _make_module()
        mod.start()
        try:
            _wait_for_server(_TEST_PORT)
        finally:
            mod.stop()

    def test_stop_is_idempotent(self) -> None:
        """Calling stop() twice must not raise."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)
        finally:
            module.stop()
        module.stop()


class TestClickMessages:
    def test_click_publishes_point_stamped(self) -> None:
        """A single click publishes one PointStamped with correct coords."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.clicked_point.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_click(1.5, 2.5, 0.0, "/world", timestamp_ms=1000)
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()

        assert len(received) == 1
        pt = received[0]
        assert pt.x == pytest.approx(1.5)
        assert pt.y == pytest.approx(2.5)
        assert pt.z == pytest.approx(0.0)

    def test_click_sets_frame_id_from_entity_path(self) -> None:
        """entity_path is stored as frame_id on the published PointStamped."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.clicked_point.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_click(0.0, 0.0, 0.0, "/robot/base", timestamp_ms=2000)
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()
        assert received
        assert received[0].frame_id == "/robot/base"

    def test_click_timestamp_converted_from_ms(self) -> None:
        """timestamp_ms is converted to seconds on PointStamped.ts."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.clicked_point.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_click(0.0, 0.0, 0.0, "", timestamp_ms=5000)
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()
        assert received
        assert received[0].ts == pytest.approx(5.0)

    def test_multiple_clicks_all_published(self) -> None:
        """A burst of clicks all arrive on the stream."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            all_arrived = threading.Event()

            def _cb(pt: Any) -> None:
                received.append(pt)
                if len(received) >= 3:
                    all_arrived.set()

            unsub = module.clicked_point.subscribe(_cb)

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_click(1.0, 0.0, 0.0)
                pub.send_click(2.0, 0.0, 0.0)
                pub.send_click(3.0, 0.0, 0.0)
                pub.flush()

            all_arrived.wait(timeout=3.0)
            unsub.dispose()
        finally:
            module.stop()

        assert sorted(pt.x for pt in received) == [1.0, 2.0, 3.0]


class TestNonClickMessages:
    def test_heartbeat_does_not_publish(self) -> None:
        """Heartbeat messages must not trigger a clicked_point publish."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            clicks: list[Any] = []
            twists: list[Any] = []
            twist_done = threading.Event()
            unsub_click = module.clicked_point.subscribe(clicks.append)
            unsub_twist = module.tele_cmd_vel.subscribe(_collect(twists, twist_done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_heartbeat(9999)
                pub.send_stop()
                pub.flush()

            twist_done.wait(timeout=2.0)
            unsub_click.dispose()
            unsub_twist.dispose()
        finally:
            module.stop()
        assert clicks == []

    def test_twist_does_not_publish_clicked_point(self) -> None:
        """Twist messages must not trigger a clicked_point publish."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            clicks: list[Any] = []
            twists: list[Any] = []
            twist_done = threading.Event()
            unsub_click = module.clicked_point.subscribe(clicks.append)
            unsub_twist = module.tele_cmd_vel.subscribe(_collect(twists, twist_done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_twist(0.5, 0.0, 0.0, 0.0, 0.0, 0.8)
                pub.flush()

            twist_done.wait(timeout=2.0)
            unsub_click.dispose()
            unsub_twist.dispose()
        finally:
            module.stop()
        assert clicks == []

    def test_stop_does_not_publish_clicked_point(self) -> None:
        """Stop messages must not trigger a clicked_point publish."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            clicks: list[Any] = []
            twists: list[Any] = []
            twist_done = threading.Event()
            unsub_click = module.clicked_point.subscribe(clicks.append)
            unsub_twist = module.tele_cmd_vel.subscribe(_collect(twists, twist_done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_stop()
                pub.flush()

            twist_done.wait(timeout=2.0)
            unsub_click.dispose()
            unsub_twist.dispose()
        finally:
            module.stop()
        assert clicks == []

    def test_twist_publishes_on_tele_cmd_vel(self) -> None:
        """Twist messages publish a Twist on the tele_cmd_vel stream."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.tele_cmd_vel.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_twist(0.5, 0.0, 0.0, 0.0, 0.0, 0.8)
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()

        assert len(received) == 1
        tw = received[0]
        assert tw.linear.x == pytest.approx(0.5)
        assert tw.angular.z == pytest.approx(0.8)

    def test_cmd_vel_scaling_applied_per_dimension(self) -> None:
        """cmd_vel_scaling multiplies each component independently."""
        from dimos.visualization.rerun.websocket_server import CmdVelScaling

        module = _make_module(
            cmd_vel_scaling=CmdVelScaling(x=0.5, y=2.0, z=0.0, roll=1.0, pitch=3.0, yaw=0.25)
        )
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.tele_cmd_vel.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_twist(1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()

        assert len(received) == 1
        tw = received[0]
        assert tw.linear.x == pytest.approx(0.5)
        assert tw.linear.y == pytest.approx(2.0)
        assert tw.linear.z == pytest.approx(0.0)
        assert tw.angular.x == pytest.approx(1.0)
        assert tw.angular.y == pytest.approx(3.0)
        assert tw.angular.z == pytest.approx(0.25)

    def test_cmd_vel_scaling_default_is_identity(self) -> None:
        """Default CmdVelScaling() must pass twists through untouched."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.tele_cmd_vel.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_twist(0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()

        assert len(received) == 1
        tw = received[0]
        assert tw.linear.x == pytest.approx(0.3)
        assert tw.linear.y == pytest.approx(0.4)
        assert tw.linear.z == pytest.approx(0.5)
        assert tw.angular.x == pytest.approx(0.6)
        assert tw.angular.y == pytest.approx(0.7)
        assert tw.angular.z == pytest.approx(0.8)

    def test_stop_publishes_zero_twist_on_tele_cmd_vel(self) -> None:
        """Stop messages publish a zero Twist on the tele_cmd_vel stream."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()
            unsub = module.tele_cmd_vel.subscribe(_collect(received, done))

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_stop()
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()

        assert len(received) == 1
        tw = received[0]
        assert tw.is_zero()

    def test_invalid_json_does_not_crash(self) -> None:
        """Malformed JSON is silently dropped; server stays alive."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            async def _send_bad() -> None:
                async with ws_client.connect(f"ws://127.0.0.1:{_TEST_PORT}/ws") as ws:
                    await ws.send("this is not json {{")
                    await asyncio.sleep(0.1)
                    await ws.send(json.dumps({"type": "heartbeat", "timestamp_ms": 0}))
                    await asyncio.sleep(0.1)

            asyncio.run(_send_bad())
        finally:
            module.stop()

    def test_mixed_message_sequence(self) -> None:
        """Realistic sequence: heartbeat → click → twist → stop publishes one point."""
        module = _make_module()
        module.start()
        try:
            _wait_for_server(_TEST_PORT)

            received: list[Any] = []
            done = threading.Event()

            def _cb(pt: Any) -> None:
                received.append(pt)
                done.set()

            unsub = module.clicked_point.subscribe(_cb)

            with MockViewerPublisher(f"ws://127.0.0.1:{_TEST_PORT}/ws") as pub:
                pub.send_heartbeat(1000)
                pub.send_click(7.0, 8.0, 9.0, "/map", timestamp_ms=1100)
                pub.send_twist(0.3, 0.0, 0.0, 0.0, 0.0, 0.2)
                pub.send_stop()
                pub.flush()

            done.wait(timeout=2.0)
            unsub.dispose()
        finally:
            module.stop()

        assert len(received) == 1
        assert received[0].x == pytest.approx(7.0)
        assert received[0].y == pytest.approx(8.0)
        assert received[0].z == pytest.approx(9.0)
