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

"""R1 Pro dual-arm blueprints (single URDF, two 7-DOF arms).

Mock blueprints (no real hardware):
    dimos run r1pro-dual-mock             # Mock coordinator only
    dimos run r1pro-planner-coordinator   # Planner + coordinator (plan & execute)

Real-hardware blueprints (requires R1 Pro on network):
    dimos run r1pro-dual-real             # Real arms via ROS 2
    dimos run r1pro-planner-real          # Planner + real arms
    dimos run r1pro-keyboard-teleop       # Chassis keyboard control (WASD)
    dimos run r1pro-keyboard-teleop-full  # Chassis + arms

Robot-side prerequisites (must be running before starting):
  1. CAN bus:  ``bash ~/can.sh``
  2. Stack:    ``./robot_startup.sh boot ... R1PROBody.d/``
  3. Remap:    ``remappings=[('/controller', '/controller_unused')]``
  4. Gate 2:   mode=5 publisher on ``/controller_unused``
"""

from __future__ import annotations

from dimos.control.components import (
    HardwareComponent,
    HardwareType,
    make_twist_base_joints,
)
from dimos.control.coordinator import ControlCoordinator, TaskConfig
from dimos.core.blueprints import autoconnect
from dimos.core.transport import LCMTransport
from dimos.manipulation.manipulation_module import ManipulationModule
from dimos.msgs.geometry_msgs.Twist import Twist
from dimos.msgs.sensor_msgs.JointState import JointState
from dimos.robot.catalog.galaxea import r1pro_arm
from dimos.robot.unitree.keyboard_teleop import KeyboardTeleop

# ---------------------------------------------------------------------------
# Robot configs
# ---------------------------------------------------------------------------

_left = r1pro_arm(side="left")
_right = r1pro_arm(side="right")

_left_hw = r1pro_arm(side="left", adapter_type="r1pro_arm", add_gripper=True)
_right_hw = r1pro_arm(side="right", adapter_type="r1pro_arm", add_gripper=True)


def _r1pro_chassis(hw_id: str = "base") -> HardwareComponent:
    """R1 Pro 3-wheel swerve chassis (holonomic twist base)."""
    return HardwareComponent(
        hardware_id=hw_id,
        hardware_type=HardwareType.BASE,
        joints=make_twist_base_joints(hw_id),
        adapter_type="r1pro_chassis",
    )


_base_joints = make_twist_base_joints("base")

# ---------------------------------------------------------------------------
# Mock blueprints (no real hardware)
# ---------------------------------------------------------------------------

# Mock dual-arm coordinator (no planner, no visualization)
r1pro_dual_mock = ControlCoordinator.blueprint(
    hardware=[_left.to_hardware_component(), _right.to_hardware_component()],
    tasks=[_left.to_task_config(), _right.to_task_config()],
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
    }
)

# Planner + coordinator (plan, preview in Meshcat, execute via mock adapters)
r1pro_planner_coordinator = autoconnect(
    ManipulationModule.blueprint(
        robots=[
            _left.to_robot_model_config(),
            _right.to_robot_model_config(),
        ],
        planning_timeout=10.0,
        enable_viz=True,
    ),
    ControlCoordinator.blueprint(
        tick_rate=100.0,
        publish_joint_state=True,
        joint_state_frame_id="coordinator",
        hardware=[_left.to_hardware_component(), _right.to_hardware_component()],
        tasks=[_left.to_task_config(), _right.to_task_config()],
    ),
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
    }
)

# ---------------------------------------------------------------------------
# Real-hardware blueprints (R1 Pro via ROS 2)
# ---------------------------------------------------------------------------

# Real dual-arm coordinator (arms only — bench testing, no chassis)
r1pro_dual_real = ControlCoordinator.blueprint(
    hardware=[_left_hw.to_hardware_component(), _right_hw.to_hardware_component()],
    tasks=[_left_hw.to_task_config(), _right_hw.to_task_config()],
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
    }
)

# Real planner + coordinator (plan & execute on real arms)
r1pro_planner_real = autoconnect(
    ManipulationModule.blueprint(
        robots=[
            _left_hw.to_robot_model_config(),
            _right_hw.to_robot_model_config(),
        ],
        planning_timeout=10.0,
        enable_viz=True,
    ),
    ControlCoordinator.blueprint(
        tick_rate=100.0,
        publish_joint_state=True,
        joint_state_frame_id="coordinator",
        hardware=[_left_hw.to_hardware_component(), _right_hw.to_hardware_component()],
        tasks=[_left_hw.to_task_config(), _right_hw.to_task_config()],
    ),
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
    }
)

# Full robot: planner + real arms + chassis + WASD base teleop
r1pro_full = autoconnect(
    ManipulationModule.blueprint(
        robots=[
            _left_hw.to_robot_model_config(),
            _right_hw.to_robot_model_config(),
        ],
        planning_timeout=10.0,
        enable_viz=True,
    ),
    ControlCoordinator.blueprint(
        tick_rate=100.0,
        publish_joint_state=True,
        joint_state_frame_id="coordinator",
        hardware=[
            _left_hw.to_hardware_component(),
            _right_hw.to_hardware_component(),
            _r1pro_chassis(),
        ],
        tasks=[
            _left_hw.to_task_config(),
            _right_hw.to_task_config(),
            TaskConfig(
                name="vel_base",
                type="velocity",
                joint_names=_base_joints,
                priority=10,
            ),
        ],
    ),
    KeyboardTeleop.blueprint(),  # WASD for chassis only, manipulation client for arms
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
        ("twist_command", Twist): LCMTransport("/cmd_vel", Twist),
    }
)

# ---------------------------------------------------------------------------
# Keyboard teleop blueprints (chassis control via WASD/QE)
# ---------------------------------------------------------------------------

# Chassis-only keyboard teleop
r1pro_keyboard_teleop = autoconnect(
    ControlCoordinator.blueprint(
        hardware=[_r1pro_chassis()],
        tasks=[
            TaskConfig(
                name="vel_base",
                type="velocity",
                joint_names=_base_joints,
                priority=10,
            ),
        ],
    ),
    KeyboardTeleop.blueprint(),
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
        ("twist_command", Twist): LCMTransport("/cmd_vel", Twist),
    }
)

# Full robot keyboard teleop (chassis + arms connected)
r1pro_keyboard_teleop_full = autoconnect(
    ControlCoordinator.blueprint(
        hardware=[
            _left_hw.to_hardware_component(),
            _right_hw.to_hardware_component(),
            _r1pro_chassis(),
        ],
        tasks=[
            _left_hw.to_task_config(),
            _right_hw.to_task_config(),
            TaskConfig(
                name="vel_base",
                type="velocity",
                joint_names=_base_joints,
                priority=10,
            ),
        ],
    ),
    KeyboardTeleop.blueprint(),
).transports(
    {
        ("joint_state", JointState): LCMTransport("/coordinator/joint_state", JointState),
        ("twist_command", Twist): LCMTransport("/cmd_vel", Twist),
    }
)



__all__ = [
    "r1pro_dual_mock",
    "r1pro_dual_real",
    "r1pro_full",
    "r1pro_keyboard_teleop",
    "r1pro_keyboard_teleop_full",
    "r1pro_planner_coordinator",
    "r1pro_planner_real",
]
