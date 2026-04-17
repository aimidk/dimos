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

"""Unitree Go2 adapter — wraps Unitree SDK2 for quadruped base control.

Init sequence: ChannelFactoryInitialize → MotionSwitcher.SelectMode("normal")
→ SportClient.StandUp → FreeWalk → Move(vx, vy, wz).

On current firmware the sport service does not auto-start; MotionSwitcher must
select a mode before SportClient RPCs reach a peer.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dimos.hardware.drive_trains.registry import TwistBaseAdapterRegistry

logger = logging.getLogger(__name__)


class UnitreeGo2Adapter:
    """TwistBaseAdapter for Unitree Go2 — 3 DOF velocity [vx, vy, wz]."""

    def __init__(
        self, dof: int = 3, network_interface: str | int | None = None, **_: object
    ) -> None:
        if dof != 3:
            raise ValueError(f"Go2 only supports 3 DOF (vx, vy, wz), got {dof}")

        self._network_interface = network_interface
        self._client = None
        self._motion_switcher = None
        self._state_subscriber = None
        self._connected = False
        self._enabled = False
        self._locomotion_ready = False
        self._lock = threading.Lock()
        self._last_velocities = [0.0, 0.0, 0.0]
        self._latest_state = None

    def connect(self) -> bool:
        try:
            from unitree_sdk2py.comm.motion_switcher.motion_switcher_client import (
                MotionSwitcherClient,
            )
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
            from unitree_sdk2py.go2.sport.sport_client import SportClient
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_

            logger.warning(f"[Go2] Initializing DDS with network_interface={self._network_interface!r}")
            if self._network_interface is None:
                ChannelFactoryInitialize(0)
            else:
                ChannelFactoryInitialize(0, str(self._network_interface))

            def state_callback(msg: SportModeState_) -> None:
                with self._lock:
                    self._latest_state = msg

            self._state_subscriber = ChannelSubscriber("rt/sportmodestate", SportModeState_)
            self._state_subscriber.Init(state_callback, 10)

            # MotionSwitcher must select a mode BEFORE SportClient.Init, else
            # every SportClient RPC returns RPC_ERR_CLIENT_SEND (3102).
            logger.warning("[Go2] Connecting to MotionSwitcherClient...")
            self._motion_switcher = MotionSwitcherClient()
            self._motion_switcher.SetTimeout(5.0)
            self._motion_switcher.Init()
            time.sleep(1.5)  # DDS discovery settle

            if not self._activate_sport_mode():
                logger.error("Failed to activate sport mode via MotionSwitcher")
                return False

            logger.warning("[Go2] Connecting to SportClient...")
            self._client = SportClient()
            self._client.SetTimeout(10.0)
            self._client.Init()
            time.sleep(2.0)

            self._connected = True
            logger.warning("[Go2] ✓ Connected")

            if not self._initialize_locomotion():
                logger.error("Failed to initialize locomotion mode")
                self.disconnect()
                return False

            return True

        except Exception as e:
            logger.error(f"Failed to connect to Go2: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        if self._connected and self._client:
            try:
                self._client.StopMove()
                time.sleep(0.5)
                logger.info("Standing down Go2...")
                self._client.StandDown()
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error during disconnect: {e}")

        self._connected = False
        self._enabled = False
        self._locomotion_ready = False
        self._client = None
        self._motion_switcher = None
        self._state_subscriber = None

    def is_connected(self) -> bool:
        return self._connected

    def get_dof(self) -> int:
        return 3

    def read_velocities(self) -> list[float]:
        with self._lock:
            return self._last_velocities.copy()

    def read_odometry(self) -> list[float] | None:
        with self._lock:
            if self._latest_state is None:
                return None
            try:
                state = self._latest_state
                x = float(state.position[0])
                y = float(state.position[1])
                theta = float(state.imu_state.rpy[2])
                return [x, y, theta]
            except Exception as e:
                logger.error(f"Error reading Go2 odometry: {e}")
                return None

    def write_velocities(self, velocities: list[float]) -> bool:
        if len(velocities) != 3:
            return False
        if not self._connected or not self._client:
            return False
        if not self._enabled:
            logger.warning("Go2 not enabled, ignoring velocity command")
            return False
        if not self._locomotion_ready:
            logger.warning("Go2 locomotion not ready, ignoring velocity command")
            return False

        vx, vy, wz = velocities
        with self._lock:
            self._last_velocities = list(velocities)
        return self._send_velocity(vx, vy, wz)

    def write_stop(self) -> bool:
        with self._lock:
            self._last_velocities = [0.0, 0.0, 0.0]
        if not self._connected or not self._client:
            return False
        try:
            self._client.StopMove()
            return True
        except Exception as e:
            logger.error(f"Error stopping Go2: {e}")
            return False

    def write_enable(self, enable: bool) -> bool:
        if enable:
            if not self._connected:
                logger.error("Cannot enable: not connected")
                return False
            if not self._locomotion_ready:
                logger.info("Locomotion not ready, initializing...")
                if not self._initialize_locomotion():
                    logger.error("Failed to initialize locomotion")
                    return False
            self._enabled = True
            logger.info("Go2 enabled")
            return True
        else:
            self.write_stop()
            self._enabled = False
            logger.info("Go2 disabled")
            return True

    def read_enabled(self) -> bool:
        return self._enabled

    # 'normal' is standard Go2 sport mode; others are firmware-variant fallbacks.
    _SPORT_MODE_CANDIDATES: tuple[str, ...] = ("normal", "ai", "advanced", "mcf")

    def _force_switch_to_normal(self) -> bool:
        """Force MotionSwitcher into 'normal'.

        *** UNSAFE WHILE THE ROBOT IS UPRIGHT — robot will fall. ***

        ReleaseMode() drops servo on all 12 joints and there's a ~3s gap
        before the new controller comes online (confirmed 2026-04-11). Only
        call this when the robot is sat down/damped. Not wired into connect()
        for that reason. SelectMode's return code is advisory — CheckMode is
        the source of truth.
        """
        if not self._motion_switcher:
            return False
        try:
            try:
                rel_code, _ = self._motion_switcher.ReleaseMode()
                logger.warning(f"[Go2] ReleaseMode() -> code={rel_code}")
            except Exception as e:
                logger.warning(f"[Go2] ReleaseMode() raised (continuing): {e}")
            time.sleep(1.0)

            try:
                sel_code, _ = self._motion_switcher.SelectMode("normal")
            except Exception as e:
                logger.error(f"[Go2] SelectMode('normal') raised: {e}")
                return False
            logger.warning(f"[Go2] SelectMode('normal') -> code={sel_code}")

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    code, data = self._motion_switcher.CheckMode()
                    if code == 0 and isinstance(data, dict):
                        name = (data.get("name") or "").strip()
                        if name == "normal":
                            logger.warning("[Go2] ✓ Switched to sport mode 'normal'")
                            time.sleep(2.0)
                            return True
                except Exception:
                    pass
                time.sleep(0.3)

            logger.error("[Go2] SelectMode('normal') did not take effect within 5s — try L2+B on remote, close Unitree app")
            return False
        except Exception as e:
            logger.error(f"[Go2] Error forcing switch to 'normal': {e}")
            return False

    def _poll_mode(self, want_nonempty: bool = True, timeout: float = 4.0) -> str | None:
        """Poll CheckMode() until name becomes (non-)empty. Returns final name or None on RPC failure."""
        if not self._motion_switcher:
            return None
        deadline = time.monotonic() + timeout
        last_name: str | None = None
        while time.monotonic() < deadline:
            try:
                code, data = self._motion_switcher.CheckMode()
                if code == 0 and isinstance(data, dict):
                    last_name = (data.get("name") or "").strip()
                    if want_nonempty and last_name:
                        return last_name
                    if not want_nonempty and not last_name:
                        return last_name
            except Exception:
                pass
            time.sleep(0.3)
        return last_name

    def _activate_sport_mode(self) -> bool:
        """Bring MotionSwitcher into a usable sport mode.

        Firmware boots reporting {'form': '0', 'name': ''} — sport service is
        dormant and must be coaxed into a mode before SportClient RPCs reach
        a peer. SelectMode return codes are advisory; CheckMode is truth.
        """
        if not self._motion_switcher:
            return False

        try:
            # Retry CheckMode on 3102 (RPC_ERR_CLIENT_SEND) — usually a DDS
            # discovery race, especially when the robot is already standing
            # and heavy sport-mode traffic crowds out our first RPC.
            code: int | None = None
            data: object = None
            for attempt in range(1, 7):
                try:
                    code, data = self._motion_switcher.CheckMode()
                except Exception as e:
                    logger.warning(f"[Go2] CheckMode() attempt {attempt} raised: {e}")
                    code, data = None, None
                logger.warning(f"[Go2] CheckMode() attempt {attempt} -> code={code} data={data}")
                if code == 3102:
                    time.sleep(1.0)
                    continue
                break

            if code == 3102:
                # On this firmware, MotionSwitcher RPC becomes unreachable
                # once sport mode is already active and the robot is standing
                # — something holds the motion_switcher channel exclusively.
                # If rt/sportmodestate is publishing, sport service is live
                # and we can skip MotionSwitcher and use SportClient directly.
                with self._lock:
                    have_state = self._latest_state is not None
                if have_state:
                    logger.warning("[Go2] MotionSwitcher unreachable (3102) but sportmodestate publishing — skipping to SportClient")
                    return True
                logger.error(f"[Go2] MotionSwitcher CheckMode=3102 and no sportmodestate — check network_interface='{self._network_interface}' and stale DDS participants")
                return False
            if code == 0 and isinstance(data, dict):
                current = (data.get("name") or "").strip()
                if current == "normal":
                    logger.warning("[Go2] MotionSwitcher already in mode 'normal'")
                    return True
                if current:
                    # DO NOT auto-switch to 'normal': ReleaseMode() drops
                    # joint servo and the robot falls (incident 2026-04-11).
                    # Accept the current mode — user must power-cycle or sit
                    # the robot down first if they want to switch.
                    logger.warning(f"[Go2] In non-'normal' mode '{current}' (likely mcf). Move() may race with onboard controller. Power-cycle or press L2+B. NOT auto-switching (robot would fall)")
                    return True

            for attempt, name in enumerate(self._SPORT_MODE_CANDIDATES):
                logger.warning(f"[Go2] SelectMode('{name}')...")
                try:
                    sel_code, _ = self._motion_switcher.SelectMode(name)
                except Exception as e:
                    logger.warning(f"[Go2] SelectMode('{name}') raised: {e}")
                    continue
                logger.warning(f"[Go2] SelectMode('{name}') -> code={sel_code}")

                if sel_code == 3102:
                    logger.error(f"[Go2] SelectMode RPC failed (3102) — check network_interface='{self._network_interface}'")
                    return False

                active = self._poll_mode(want_nonempty=True, timeout=4.0)
                if active:
                    logger.warning(f"[Go2] ✓ Sport mode '{active}' active (requested '{name}', code={sel_code})")
                    time.sleep(2.0)
                    return True

                logger.warning(f"[Go2] SelectMode('{name}') did not activate — trying next")

                try:
                    rel_code, _ = self._motion_switcher.ReleaseMode()
                    logger.warning(f"[Go2] ReleaseMode() -> code={rel_code}")
                except Exception:
                    pass
                time.sleep(0.5)

            logger.error(f"[Go2] None of {self._SPORT_MODE_CANDIDATES} activated — exit AI mode (L2+B), close Unitree app, or try motion_switcher_example.py manually")
            return False

        except Exception as e:
            logger.error(f"[Go2] Error activating sport mode: {e}")
            return False

    def _initialize_locomotion(self) -> bool:
        """StandUp → FreeWalk. Assumes sport mode already selected."""
        if not self._client:
            return False

        if not self._activate_sport_mode():
            return False

        try:
            logger.info("Standing up Go2...")
            ret = None
            # Retry StandUp on 3102 (RPC_ERR_CLIENT_SEND) — DDS discovery race.
            for attempt in range(1, 6):
                ret = self._client.StandUp()
                if ret == 0:
                    break
                if ret == 3102:
                    logger.warning(f"StandUp() attempt {attempt} got 3102 — retrying")
                    time.sleep(1.0)
                    continue
                break

            if ret != 0:
                logger.error(f"StandUp() failed with code {ret} — if 3102, exit AI mode (L2+B) and close Unitree app")
                return False
            time.sleep(3)

            logger.info("Activating FreeWalk locomotion mode...")
            ret = self._client.FreeWalk()
            if ret != 0:
                logger.error(f"FreeWalk() failed with code {ret}")
                return False
            time.sleep(2)

            self._locomotion_ready = True
            logger.info("✓ Go2 locomotion ready")
            return True

        except Exception as e:
            logger.error(f"Error initializing locomotion: {e}")
            return False

    def _send_velocity(self, vx: float, vy: float, wz: float) -> bool:
        try:
            with self._lock:
                assert self._client is not None
                ret = self._client.Move(vx, vy, wz)

            if ret != 0:
                logger.warning(f"Move() returned error code {ret}")
                return False

            return True

        except Exception as e:
            logger.error(f"Error sending Go2 velocity: {e}")
            return False


def register(registry: TwistBaseAdapterRegistry) -> None:
    registry.register("unitree_go2", UnitreeGo2Adapter)


__all__ = ["UnitreeGo2Adapter"]
