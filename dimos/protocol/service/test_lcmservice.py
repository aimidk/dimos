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

import threading
from unittest.mock import MagicMock, create_autospec

from lcm import LCM
import pytest

from dimos.protocol.pubsub.impl.lcmpubsub import LCMPubSubBase, Topic
from dimos.protocol.service.lcmservice import (
    _DEFAULT_LCM_URL,
    LCMConfig,
    LCMService,
    autoconf,
)
from dimos.protocol.service.system_configurator.lcm import (
    BufferConfiguratorLinux,
    BufferConfiguratorMacOS,
    MaxFileConfiguratorMacOS,
    MulticastConfiguratorLinux,
    MulticastConfiguratorMacOS,
)
from dimos.protocol.service.system_configurator.libpython import LibPythonConfiguratorMacOS


class BlockingSubscription:
    def __init__(self) -> None:
        self.queue_capacity: int | None = None

    def set_queue_capacity(self, queue_capacity: int) -> None:
        self.queue_capacity = queue_capacity


class BlockingLCM:
    def __init__(self) -> None:
        self.handle_entered = threading.Event()
        self.release_handle = threading.Event()
        self.publish_called = threading.Event()
        self.subscribe_called = threading.Event()
        self.unsubscribe_called = threading.Event()
        self.subscription = BlockingSubscription()

    def fileno(self) -> int:
        # Called by LCMService.start() to pre-warm LCM recv setup. A real LCM
        # sets up sockets and runs a self-test here; for the fake we just
        # return a sentinel fd since nothing will select() on it.
        return -1

    def handle_timeout(self, _timeout: int) -> None:
        self.handle_entered.set()
        self.release_handle.wait(timeout=1.0)

    def publish(self, _channel: str, _message: bytes) -> None:
        self.publish_called.set()

    def subscribe(self, _channel: str, _handler) -> BlockingSubscription:
        self.subscribe_called.set()
        return self.subscription

    def unsubscribe(self, subscription: BlockingSubscription) -> None:
        assert subscription is self.subscription
        self.unsubscribe_called.set()


@pytest.fixture
def patch_platform(mocker):
    def _patch(system_name: str) -> None:
        mocker.patch(
            "dimos.protocol.service.system_configurator.lcm_config.platform.system",
            return_value=system_name,
        )

    return _patch


@pytest.fixture
def mock_configure_system(mocker):
    return mocker.patch("dimos.protocol.service.lcmservice.configure_system")


@pytest.fixture
def mock_lcm_logger(mocker):
    return mocker.patch("dimos.protocol.service.lcmservice.logger")


@pytest.fixture
def mock_lcm_class(mocker):
    mock = mocker.patch("dimos.protocol.service.lcmservice.lcm_mod.LCM")
    mock.return_value = create_autospec(LCM, spec_set=True, instance=True)
    return mock


@pytest.fixture
def fake_lcm(mocker):
    fake = BlockingLCM()
    mocker.patch("dimos.protocol.service.lcmservice.lcm_mod.LCM", return_value=fake)
    return fake


class TestConfigureSystemForLcm:
    def test_creates_linux_checks_on_linux(self, patch_platform, mock_configure_system) -> None:
        patch_platform("Linux")
        autoconf()
        mock_configure_system.assert_called_once()
        checks = mock_configure_system.call_args[0][0]
        assert len(checks) == 2
        assert isinstance(checks[0], MulticastConfiguratorLinux)
        assert isinstance(checks[1], BufferConfiguratorLinux)
        assert checks[0].loopback_interface == "lo"

    def test_creates_macos_checks_on_darwin(self, patch_platform, mock_configure_system) -> None:
        patch_platform("Darwin")
        autoconf()
        mock_configure_system.assert_called_once()
        checks = mock_configure_system.call_args[0][0]
        assert len(checks) == 4
        assert isinstance(checks[0], MulticastConfiguratorMacOS)
        assert isinstance(checks[1], BufferConfiguratorMacOS)
        assert isinstance(checks[2], MaxFileConfiguratorMacOS)
        assert isinstance(checks[3], LibPythonConfiguratorMacOS)
        assert checks[0].loopback_interface == "lo0"

    def test_passes_check_only_flag(self, patch_platform, mock_configure_system) -> None:
        patch_platform("Linux")
        autoconf(check_only=True)
        mock_configure_system.assert_called_once()
        assert mock_configure_system.call_args[1]["check_only"] is True

    def test_logs_error_on_unsupported_system(
        self, patch_platform, mock_configure_system, mock_lcm_logger
    ) -> None:
        patch_platform("Windows")
        autoconf()
        mock_configure_system.assert_not_called()
        mock_lcm_logger.error.assert_called_once()
        assert "Windows" in mock_lcm_logger.error.call_args[0][0]


class TestLCMConfig:
    def test_default_values(self) -> None:
        config = LCMConfig()
        assert config.ttl == 0
        assert config.url == _DEFAULT_LCM_URL
        assert config.lcm is None

    def test_custom_url(self) -> None:
        custom_url = "udpm://192.168.1.1:7777?ttl=1"
        config = LCMConfig(url=custom_url)
        assert config.url == custom_url


class TestTopic:
    def test_str_without_lcm_type(self) -> None:
        topic = Topic(topic="my_topic")
        assert str(topic) == "my_topic"

    def test_str_with_lcm_type(self) -> None:
        mock_type = MagicMock()
        mock_type.msg_name = "TestMessage"
        topic = Topic(topic="my_topic", lcm_type=mock_type)
        assert str(topic) == "my_topic#TestMessage"


class TestLCMService:
    def test_init_with_defaults(self, mock_lcm_class) -> None:
        service = LCMService()
        assert service.config.url == _DEFAULT_LCM_URL
        assert service.l == mock_lcm_class.return_value
        mock_lcm_class.assert_called_once_with(_DEFAULT_LCM_URL)

    def test_init_with_custom_url(self, mock_lcm_class) -> None:
        custom_url = "udpm://192.168.1.1:7777?ttl=1"
        LCMService(url=custom_url)
        mock_lcm_class.assert_called_once_with(custom_url)

    def test_init_with_existing_lcm_instance(self, mock_lcm_class) -> None:
        mock_lcm_instance = create_autospec(LCM, spec_set=True, instance=True)
        service = LCMService(lcm=mock_lcm_instance)
        mock_lcm_class.assert_not_called()
        assert service.l == mock_lcm_instance

    def test_start_and_stop(self, mock_lcm_class) -> None:
        service = LCMService()
        service.start()

        assert service._thread is not None
        assert service._thread.is_alive()

        thread = service._thread
        service.stop()

        assert not thread.is_alive()
        assert service._thread is None

    def test_getstate_excludes_unpicklable_attrs(self, mock_lcm_class) -> None:
        service = LCMService()
        state = service.__getstate__()

        assert "l" not in state
        assert "_stop_event" not in state
        assert "_thread" not in state
        assert "_l_lock" not in state
        assert "_call_thread_pool" not in state
        assert "_call_thread_pool_lock" not in state

    def test_setstate_reinitializes_runtime_attrs(self, mock_lcm_class) -> None:
        service = LCMService()
        state = service.__getstate__()

        new_service = object.__new__(LCMService)
        new_service.__setstate__(state)

        assert new_service.l is None
        assert isinstance(new_service._stop_event, threading.Event)
        assert new_service._thread is None
        assert hasattr(new_service._l_lock, "acquire")
        assert hasattr(new_service._l_lock, "release")

    def test_start_reinitializes_lcm_after_unpickling(self, mock_lcm_class) -> None:
        service = LCMService()
        state = service.__getstate__()

        new_service = object.__new__(LCMService)
        new_service.__setstate__(state)

        new_service.start()

        assert mock_lcm_class.call_count == 2

        new_service.stop()

    def test_stop_cleans_up_lcm_instance(self, mock_lcm_class) -> None:
        service = LCMService()
        service.start()
        service.stop()

        assert service.l is None

    def test_stop_preserves_external_lcm_instance(self) -> None:
        mock_lcm_instance = create_autospec(LCM, spec_set=True, instance=True)

        service = LCMService(lcm=mock_lcm_instance)
        service.start()
        service.stop()

        assert service.l == mock_lcm_instance


@pytest.fixture
def fake_lcm_service(mock_lcm_class):
    service = LCMService()
    service.start()
    yield service
    service.stop()


def test_get_call_thread_pool_creates_pool(fake_lcm_service):
    assert fake_lcm_service._call_thread_pool is None

    pool = fake_lcm_service._get_call_thread_pool()
    assert pool is not None
    assert fake_lcm_service._call_thread_pool == pool

    pool2 = fake_lcm_service._get_call_thread_pool()
    assert pool2 is pool

    pool.shutdown(wait=False)


def test_stop_shuts_down_thread_pool(mock_lcm_class):
    service = LCMService()
    service.start()
    pool = service._get_call_thread_pool()
    assert pool is not None
    service.stop()
    assert service._call_thread_pool is None


def test_start_is_idempotent(fake_lcm_service):
    fake_lcm_service.start()

    first_thread = fake_lcm_service._thread

    fake_lcm_service.start()

    assert fake_lcm_service._thread is first_thread


def test_stop_timeout_preserves_lcm_until_loop_exits(fake_lcm, mocker):
    mocker.patch("dimos.protocol.service.lcmservice.DEFAULT_THREAD_JOIN_TIMEOUT", 0.01)

    service = LCMService()
    service.start()
    assert fake_lcm.handle_entered.wait(timeout=0.5)

    thread = service._thread
    assert thread is not None
    service.stop()

    assert service._thread is thread
    assert thread.is_alive()
    assert service.l is fake_lcm

    fake_lcm.release_handle.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert service.l is None
    assert service._thread is None


@pytest.fixture
def fake_pub_sub(fake_lcm):
    pubsub = LCMPubSubBase()
    pubsub.start()
    yield pubsub
    pubsub.stop()


def test_publish_waits_for_handle_loop(fake_lcm, fake_pub_sub):
    assert fake_lcm.handle_entered.wait(timeout=0.5)

    publisher = threading.Thread(
        target=lambda: fake_pub_sub.publish(Topic("/test"), b"payload"),
        daemon=True,
    )
    publisher.start()

    # Loop holds _l_lock inside handle_timeout; publish waits for the lock.
    assert not fake_lcm.publish_called.wait(timeout=0.1)
    assert publisher.is_alive()

    # Releasing handle_timeout drops _l_lock; publish now proceeds.
    fake_lcm.release_handle.set()

    assert fake_lcm.publish_called.wait(timeout=1.0)
    publisher.join(timeout=1.0)
    assert not publisher.is_alive()


def test_subscribe_waits_for_handle_loop(fake_lcm, fake_pub_sub):
    """subscribe() must block while the loop thread is inside handle_timeout()."""
    assert fake_lcm.handle_entered.wait(timeout=0.5)

    subscriber = threading.Thread(
        target=lambda: fake_pub_sub.subscribe(Topic("/test"), lambda *_args: None),
        daemon=True,
    )
    subscriber.start()

    assert not fake_lcm.subscribe_called.wait(timeout=0.1)
    assert subscriber.is_alive()

    fake_lcm.release_handle.set()

    assert fake_lcm.subscribe_called.wait(timeout=1.0)
    subscriber.join(timeout=1.0)
    assert not subscriber.is_alive()
    assert fake_lcm.subscription.queue_capacity == 10000


def test_unsubscribe_waits_for_handle_loop(fake_lcm, fake_pub_sub):
    """unsubscribe() must block while the loop thread is inside handle_timeout().

    This is the specific race whose resolution fixes the segfault in
    pylcm.c. Unsubscribing from another thread while dispatch is running
    would set subs_obj->lcm_obj = NULL under the nose of pylcm_msg_handler.
    """
    # Let the first handle_timeout complete so we can subscribe cleanly.
    assert fake_lcm.handle_entered.wait(timeout=0.5)
    fake_lcm.release_handle.set()

    unsubscribe_holder: dict[str, object] = {}

    def do_subscribe() -> None:
        unsubscribe_holder["fn"] = fake_pub_sub.subscribe(Topic("/test"), lambda *_args: None)

    subscriber = threading.Thread(target=do_subscribe, daemon=True)
    subscriber.start()
    assert fake_lcm.subscribe_called.wait(timeout=1.0)
    subscriber.join(timeout=1.0)
    assert not subscriber.is_alive()

    # Reset gates so the next handle_timeout iteration blocks again.
    fake_lcm.handle_entered.clear()
    fake_lcm.release_handle.clear()
    assert fake_lcm.handle_entered.wait(timeout=1.0)

    unsubscribe = unsubscribe_holder["fn"]
    unsub_thread = threading.Thread(target=unsubscribe, daemon=True)  # type: ignore[arg-type]
    unsub_thread.start()

    # Loop holds _l_lock; unsubscribe waits for the lock.
    assert not fake_lcm.unsubscribe_called.wait(timeout=0.1)
    assert unsub_thread.is_alive()

    fake_lcm.release_handle.set()

    assert fake_lcm.unsubscribe_called.wait(timeout=1.0)
    unsub_thread.join(timeout=1.0)
    assert not unsub_thread.is_alive()


def test_stop_from_within_lcm_thread(mocker):
    """stop() called from inside handle_timeout must not deadlock and must
    let the loop clean up when it eventually exits."""
    service_holder: dict[str, LCMService] = {}
    captured: dict[str, threading.Thread] = {}

    class SelfStoppingLCM:
        def __init__(self) -> None:
            self.done = threading.Event()

        def fileno(self) -> int:
            return -1

        def handle_timeout(self, _timeout: int) -> None:
            if not self.done.is_set():
                captured["thread"] = threading.current_thread()
                service_holder["service"].stop()
                self.done.set()

        def publish(self, *_args: object) -> None:
            pass

        def subscribe(self, *_args: object) -> MagicMock:
            return MagicMock()

        def unsubscribe(self, *_args: object) -> None:
            pass

    fake = SelfStoppingLCM()
    mocker.patch("dimos.protocol.service.lcmservice.lcm_mod.LCM", return_value=fake)

    service = LCMService()
    service_holder["service"] = service
    service.start()

    assert fake.done.wait(timeout=2.0)

    thread = captured["thread"]
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert service.l is None
    assert service._thread is None


def test_handler_can_publish_via_rlock_reentry(mocker):
    """A message handler dispatched from handle_timeout runs on the loop
    thread while it already holds _l_lock. Reentry must work so the handler
    can call self.publish/subscribe/unsubscribe. This is why _l_lock is an
    RLock rather than a plain Lock.
    """
    publish_calls: list[tuple[str, bytes]] = []
    handler_done = threading.Event()

    class ReentrantLCM:
        def __init__(self) -> None:
            self._handler = None
            self._dispatched = False
            self._subscription = BlockingSubscription()

        def fileno(self) -> int:
            return -1

        def handle_timeout(self, _timeout: int) -> None:
            # Dispatch one fake message on the first call after a handler
            # has been registered. The handler will call self.publish, which
            # must reenter _l_lock recursively on the loop thread.
            if self._handler is not None and not self._dispatched:
                self._dispatched = True
                self._handler("/req", b"req-payload")
                handler_done.set()

        def publish(self, channel: str, message: bytes) -> None:
            publish_calls.append((channel, message))

        def subscribe(self, _channel, handler) -> BlockingSubscription:
            self._handler = handler
            return self._subscription

        def unsubscribe(self, _subscription) -> None:
            pass

    fake = ReentrantLCM()
    mocker.patch("dimos.protocol.service.lcmservice.lcm_mod.LCM", return_value=fake)

    pubsub = LCMPubSubBase()
    pubsub.start()

    def reentrant_callback(_msg: bytes, _topic: Topic) -> None:
        pubsub.publish(Topic("/res"), b"res-payload")

    pubsub.subscribe(Topic("/req"), reentrant_callback)

    assert handler_done.wait(timeout=2.0)
    pubsub.stop()

    assert ("/res", b"res-payload") in publish_calls
