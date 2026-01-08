#!/usr/bin/env python3

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

"""
PubSub Benchmark Tests - Compare throughput across transports.

Run with: pytest -m benchmark -v -s dimos/protocol/pubsub/test_spec_benchmark.py
"""

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
import pickle
import threading
import time
from typing import Any

import pytest

from dimos.msgs.geometry_msgs import Vector3
from dimos.msgs.sensor_msgs.Image import Image
from dimos.protocol.pubsub.lcmpubsub import LCM, Topic
from dimos.protocol.pubsub.memory import Memory
from dimos.protocol.pubsub.shmpubsub import PickleSharedMemory
from dimos.utils.data import get_data

# =============================================================================
# Benchmark Results Collection
# =============================================================================


@dataclass
class BenchmarkResult:
    transport: str
    payload: str
    duration: float
    sent: int
    received: int
    msg_size_bytes: int
    throughput_msg_s: float
    throughput_gb_s: float


@dataclass
class BenchmarkResults:
    results: list[BenchmarkResult] = field(default_factory=list)

    def add(self, result: BenchmarkResult) -> None:
        self.results.append(result)

    def print_summary(self) -> None:
        if not self.results:
            return

        print("\n")
        print("=" * 75)
        print("BENCHMARK SUMMARY")
        print("=" * 75)

        # Group by payload type
        payloads = sorted(set(r.payload for r in self.results))

        for payload in payloads:
            payload_results = [r for r in self.results if r.payload == payload]
            if not payload_results:
                continue

            # Get size from first result
            size_bytes = payload_results[0].msg_size_bytes
            if size_bytes >= 1024 * 1024:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            elif size_bytes >= 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes} B"

            print(f"\n## {payload} ({size_str})\n")
            print(
                f"{'Transport':<12} {'GB/sec':>10} {'msgs/sec':>12} {'Sent':>10} {'Received':>10}"
            )
            print("-" * 58)
            for r in sorted(payload_results, key=lambda x: -x.throughput_gb_s):
                print(
                    f"{r.transport:<12} {r.throughput_gb_s:>10.2f} {r.throughput_msg_s:>12,.0f} "
                    f"{r.sent:>10,} {r.received:>10,}"
                )

        print("\n" + "=" * 75)


@pytest.fixture(scope="module")
def benchmark_results():
    """Module-scoped fixture to collect benchmark results."""
    results = BenchmarkResults()
    yield results
    results.print_summary()


# =============================================================================
# Context Managers for each transport
# =============================================================================


@contextmanager
def memory_context():
    """Context manager for Memory PubSub implementation."""
    yield Memory()


@contextmanager
def lcm_context():
    lcm_pubsub = LCM(autoconf=True)
    lcm_pubsub.start()
    yield lcm_pubsub
    lcm_pubsub.stop()


@contextmanager
def shm_context():
    shm_pubsub = PickleSharedMemory(prefer="cpu")
    shm_pubsub.start()
    yield shm_pubsub
    shm_pubsub.stop()


# ROS context - only available in devcontainer with ROS installed
ROS_AVAILABLE = False
ros_context = None

try:
    from dimos.protocol.pubsub.rospubsub import ROS, ROS_AVAILABLE, ROSTopic

    if ROS_AVAILABLE:

        @contextmanager
        def ros_context():
            ros_pubsub = ROS(node_name="benchmark_ros_pubsub")
            ros_pubsub.start()
            time.sleep(0.1)  # Give ROS time to initialize
            yield ros_pubsub
            ros_pubsub.stop()

except ImportError:
    pass


# =============================================================================
# Test Data Builders
# =============================================================================


def _get_size_bytes(obj: Any) -> int:
    """Get size of object in bytes."""
    if isinstance(obj, bytes):
        return len(obj)
    if hasattr(obj, "data") and hasattr(obj.data, "nbytes"):
        return obj.data.nbytes
    if hasattr(obj, "__sizeof__"):
        return obj.__sizeof__()
    return 0


def _load_test_image() -> Image:
    """Load and resize test image to ~900KB."""
    img_path = get_data("cafe.jpg")
    img = Image.from_file(img_path)
    return img.resize(640, 480)


# Build test data: (context, transport_name, topic, message, msg_size, payload_label)
def _build_testdata() -> list[tuple[Callable[[], Any], str, Any, Any, int, str]]:
    testdata = []

    # --- Small messages ---
    small_msg_label = "Small message"

    # Memory - small
    testdata.append(
        (memory_context, "memory", "bench_small", "hello", len("hello"), small_msg_label)
    )

    # LCM - small (Vector3)
    vec = Vector3(1.0, 2.0, 3.0)
    testdata.append(
        (
            lcm_context,
            "lcm",
            Topic(topic="/bench_small", lcm_type=Vector3),
            vec,
            24,
            small_msg_label,
        )
    )

    # SHM - small
    shm_small = b"hello"
    testdata.append(
        (shm_context, "shm", "/bench_small_shm", shm_small, len(shm_small), small_msg_label)
    )

    # ROS - small
    if ROS_AVAILABLE and ros_context is not None:
        try:
            from std_msgs.msg import String as ROSString

            ros_msg = ROSString(data="hello")
            testdata.append(
                (
                    ros_context,
                    "ros",
                    ROSTopic(topic="/bench_small_ros", ros_type=ROSString),
                    ros_msg,
                    5,
                    small_msg_label,
                )
            )
        except ImportError:
            pass

    # --- Image messages (~900KB) ---
    img_label = "Image 640x480"
    img = _load_test_image()
    img_size = _get_size_bytes(img)

    # Memory - image
    testdata.append((memory_context, "memory", "bench_image", img, img_size, img_label))

    # LCM - image
    testdata.append(
        (lcm_context, "lcm", Topic(topic="/bench_image", lcm_type=Image), img, img_size, img_label)
    )

    # SHM - image (pickled)
    img_pickled = pickle.dumps(img)
    testdata.append(
        (shm_context, "shm", "/bench_image_shm", img_pickled, len(img_pickled), img_label)
    )

    # ROS - image
    if ROS_AVAILABLE and ros_context is not None:
        try:
            from sensor_msgs.msg import Image as ROSImage

            ros_img = ROSImage()
            ros_img.height = img.height
            ros_img.width = img.width
            ros_img.encoding = "bgr8"
            ros_img.step = img.width * 3
            ros_img.data = img.to_bgr().data.tobytes()
            testdata.append(
                (
                    ros_context,
                    "ros",
                    ROSTopic(topic="/bench_image_ros", ros_type=ROSImage),
                    ros_img,
                    img_size,
                    img_label,
                )
            )
        except ImportError:
            pass

    return testdata


# Build test data at module load
benchmark_testdata = _build_testdata()


# =============================================================================
# Benchmark Test
# =============================================================================


@pytest.mark.benchmark
@pytest.mark.parametrize(
    "pubsub_context, transport_name, topic, message, msg_size, payload_label",
    benchmark_testdata,
    ids=[f"{t[1]}-{t[5].replace(' ', '_')}" for t in benchmark_testdata],
)
def test_throughput(
    pubsub_context,
    transport_name,
    topic,
    message,
    msg_size,
    payload_label,
    benchmark_results,
) -> None:
    """Measure throughput: send messages for 5 seconds, calculate GB/s and msgs/s."""
    duration = 5.0

    with pubsub_context() as pubsub:
        received_count = [0]
        msg_received = threading.Event()

        def callback(msg: Any, _topic: Any) -> None:
            received_count[0] += 1
            msg_received.set()

        pubsub.subscribe(topic, callback)

        # Send messages synchronously for `duration` seconds
        start_time = time.time()
        sent_count = 0

        while time.time() - start_time < duration:
            msg_received.clear()
            pubsub.publish(topic, message)
            sent_count += 1
            if not msg_received.wait(timeout=1.0):
                break

        elapsed = time.time() - start_time
        recv_count = received_count[0]
        recv_bytes = recv_count * msg_size
        throughput_msg_s = recv_count / elapsed if elapsed > 0 else 0
        throughput_gb_s = recv_bytes / (elapsed * 1_000_000_000) if elapsed > 0 else 0

        benchmark_results.add(
            BenchmarkResult(
                transport=transport_name,
                payload=payload_label,
                duration=elapsed,
                sent=sent_count,
                received=recv_count,
                msg_size_bytes=msg_size,
                throughput_msg_s=throughput_msg_s,
                throughput_gb_s=throughput_gb_s,
            )
        )

        assert recv_count > 0, f"No messages received for {transport_name}"
