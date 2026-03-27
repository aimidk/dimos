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

from __future__ import annotations

from collections.abc import Iterator
import threading

from dimos.core.module import ModuleConfig
from dimos.core.stream import In, Out
from dimos.core.transport import pLCMTransport
from dimos.memory2.module import StreamModule
from dimos.memory2.stream import Stream
from dimos.memory2.transform import Transformer
from dimos.memory2.type.observation import Observation
from dimos.utils.threadpool import get_scheduler


def test_stream_module_subclass_blueprint() -> None:
    """StreamModule subclass creates a Blueprint with correct In/Out ports."""

    class Identity(Transformer[str, str]):
        def __call__(self, upstream: Iterator[Observation[str]]) -> Iterator[Observation[str]]:
            yield from upstream

    class MyModule(StreamModule):
        pipeline = Stream().transform(Identity())
        messages: In[str]
        processed: Out[str]

    bp = MyModule.blueprint()

    assert len(bp.blueprints) == 1
    atom = bp.blueprints[0]
    stream_names = {s.name for s in atom.streams}
    assert "messages" in stream_names
    assert "processed" in stream_names


def test_stream_module_with_transformer_pipeline() -> None:
    """StreamModule accepts a bare Transformer as pipeline."""

    class Double(Transformer[int, int]):
        def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
            for obs in upstream:
                yield obs.derive(data=obs.data * 2)

    class Doubler(StreamModule):
        pipeline = Double()
        numbers: In[int]
        doubled: Out[int]

    bp = Doubler.blueprint()

    assert len(bp.blueprints) == 1
    atom = bp.blueprints[0]
    stream_names = {s.name for s in atom.streams}
    assert "numbers" in stream_names
    assert "doubled" in stream_names


def test_stream_module_with_method_pipeline() -> None:
    """StreamModule accepts a method pipeline with access to self.config."""

    class MyConfig(ModuleConfig):
        factor: int = 3

    class Double(Transformer[int, int]):
        def __init__(self, factor: int = 2) -> None:
            self.factor = factor

        def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
            for obs in upstream:
                yield obs.derive(data=obs.data * self.factor)

    class Multiplier(StreamModule[MyConfig]):
        default_config = MyConfig

        def pipeline(self, stream: Stream) -> Stream:
            return stream.transform(Double(factor=self.config.factor))

        numbers: In[int]
        result: Out[int]

    bp = Multiplier.blueprint(factor=5)

    assert len(bp.blueprints) == 1
    atom = bp.blueprints[0]
    stream_names = {s.name for s in atom.streams}
    assert "numbers" in stream_names
    assert "result" in stream_names


def test_stream_module_runtime_wiring() -> None:
    """End-to-end: push data into In port, assert transformed data on Out port."""

    class Double(Transformer[int, int]):
        def __call__(self, upstream: Iterator[Observation[int]]) -> Iterator[Observation[int]]:
            for obs in upstream:
                yield obs.derive(data=obs.data * 2)

    class Doubler(StreamModule):
        pipeline = Stream().transform(Double())
        numbers: In[int]
        doubled: Out[int]

    module = Doubler()
    module.numbers.transport = pLCMTransport("/test/numbers")
    module.doubled.transport = pLCMTransport("/test/doubled")

    received: list[int] = []
    done = threading.Event()

    unsub = module.doubled.subscribe(lambda msg: (received.append(msg), done.set()))

    module.start()
    try:
        module.numbers.transport.publish(42)
        assert done.wait(timeout=5.0), f"Timed out, received={received}"
        assert received == [84]
    finally:
        unsub()
        module.stop()
        # Shutdown the global RxPY thread pool so conftest thread-leak check passes
        get_scheduler().executor.shutdown(wait=True)
