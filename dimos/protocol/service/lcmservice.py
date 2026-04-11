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

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import platform
import threading
import traceback
from typing import Any

import lcm as lcm_mod

from dimos.constants import DEFAULT_THREAD_JOIN_TIMEOUT
from dimos.protocol.service.spec import BaseConfig, Service
from dimos.protocol.service.system_configurator.base import configure_system
from dimos.protocol.service.system_configurator.lcm_config import lcm_configurators
from dimos.utils.logging_config import setup_logger

logger = setup_logger()

_DEFAULT_LCM_HOST = "239.255.76.67"
_DEFAULT_LCM_PORT = "7667"
# LCM_DEFAULT_URL is used by LCM (we didn't pick that env var name)
_DEFAULT_LCM_URL = os.getenv(
    "LCM_DEFAULT_URL", f"udpm://{_DEFAULT_LCM_HOST}:{_DEFAULT_LCM_PORT}?ttl=0"
)


def autoconf(check_only: bool = False) -> None:
    checks = lcm_configurators()
    if not checks:
        logger.error(f"System configuration not supported on {platform.system()}")
        return
    configure_system(checks, check_only=check_only)


class LCMConfig(BaseConfig):
    ttl: int = 0
    url: str = _DEFAULT_LCM_URL
    lcm: lcm_mod.LCM | None = None


_LCM_LOOP_TIMEOUT = 50


# this class just sets up cpp LCM instance
# and runs its handle loop in a thread
# higher order stuff is done by pubsub/impl/lcmpubsub.py
class LCMService(Service):
    config: LCMConfig
    l: lcm_mod.LCM | None
    _stop_event: threading.Event
    _l_lock: threading.RLock
    _thread: threading.Thread | None
    _call_thread_pool: ThreadPoolExecutor | None = None
    _call_thread_pool_lock: threading.RLock = threading.RLock()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # we support passing an existing LCM instance
        if self.config.lcm:
            self.l = self.config.lcm
        else:
            self.l = lcm_mod.LCM(self.config.url) if self.config.url else lcm_mod.LCM()

        self._l_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None

    def __getstate__(self):  # type: ignore[no-untyped-def]
        """Exclude unpicklable runtime attributes when serializing."""
        state = self.__dict__.copy()
        # Remove unpicklable attributes
        state.pop("l", None)
        state.pop("_stop_event", None)
        state.pop("_thread", None)
        state.pop("_l_lock", None)
        state.pop("_call_thread_pool", None)
        state.pop("_call_thread_pool_lock", None)
        return state

    def __setstate__(self, state) -> None:  # type: ignore[no-untyped-def]
        """Restore object from pickled state."""
        self.__dict__.update(state)
        # Reinitialize runtime attributes
        self.l = None
        self._stop_event = threading.Event()
        self._thread = None
        self._l_lock = threading.RLock()
        self._call_thread_pool = None
        self._call_thread_pool_lock = threading.RLock()

    def _cleanup_owned_lcm(self) -> None:
        """Dispose of the internally-owned LCM instance."""
        if self.config.lcm:
            return

        with self._l_lock:
            if self.l is not None:
                del self.l
                self.l = None

    def start(self) -> None:
        with self._l_lock:
            if self._thread is not None and self._thread.is_alive():
                return

            # Reinitialize LCM if it's None (e.g., after unpickling)
            l = self.l
            if l is None:
                if self.config.lcm:
                    l = self.config.lcm
                else:
                    l = lcm_mod.LCM(self.config.url) if self.config.url else lcm_mod.LCM()
                self.l = l

            # Pre-warm LCM recv setup (including the up-to-10s self-test) now,
            # so _lcm_loop doesn't do it later while holding _l_lock, which
            # would block external publish/subscribe/unsubscribe calls.
            try:
                l.fileno()
            except Exception as e:
                logger.warning(f"LCM fileno pre-warm failed: {e}")

            self._stop_event.clear()
            self._thread = threading.Thread(target=self._lcm_loop, daemon=True)
            self._thread.start()

    def _lcm_loop(self) -> None:
        """LCM message handling loop.

        Holds _l_lock during handle_timeout so that external callers
        (publish/subscribe/unsubscribe on this LCMService from other threads)
        are serialized with dispatch. This prevents a data race in the LCM
        Python binding that segfaults otherwise.
        """
        try:
            while not self._stop_event.is_set():
                with self._l_lock:
                    if self.l is None:
                        break
                    try:
                        self.l.handle_timeout(_LCM_LOOP_TIMEOUT)
                    except Exception as e:
                        stack_trace = traceback.format_exc()
                        print(f"Error in LCM handling: {e}\n{stack_trace}")
        finally:
            self._cleanup_owned_lcm()
            with self._l_lock:
                if self._thread is threading.current_thread():
                    self._thread = None

    def stop(self) -> None:
        """Stop the LCM loop."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            # Only join if we're not the LCM thread (avoid "cannot join current thread")
            if threading.current_thread() != thread:
                thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT)
                if thread.is_alive():
                    logger.warning("LCM thread did not stop cleanly within timeout")

        # If the thread is still alive, do not clean up now. _lcm_loop will
        # clean up when it exits. If we try to clean up here as well it could
        # race with the cleanup in _lcm_loop and segfault, and it would leave
        # the service half-stopped with a live thread but no LCM instance.
        if thread is None or not thread.is_alive():
            self._cleanup_owned_lcm()

        with self._call_thread_pool_lock:
            if self._call_thread_pool:
                # Check if we're being called from within the thread pool
                # If so, we can't wait for shutdown (would cause "cannot join current thread")
                current_thread = threading.current_thread()
                is_pool_thread = False

                # Check if current thread is one of the pool's threads
                # ThreadPoolExecutor threads have names like "ThreadPoolExecutor-N_M"
                if hasattr(self._call_thread_pool, "_threads"):
                    is_pool_thread = current_thread in self._call_thread_pool._threads
                elif "ThreadPoolExecutor" in current_thread.name:
                    # Fallback: check thread name pattern
                    is_pool_thread = True

                # Don't wait if we're in a pool thread to avoid deadlock
                self._call_thread_pool.shutdown(wait=not is_pool_thread)
                self._call_thread_pool = None

    def _get_call_thread_pool(self) -> ThreadPoolExecutor:
        with self._call_thread_pool_lock:
            if self._call_thread_pool is None:
                self._call_thread_pool = ThreadPoolExecutor(max_workers=4)
            return self._call_thread_pool
