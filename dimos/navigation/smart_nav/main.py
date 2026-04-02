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

"""Configurable SmartNav blueprint builder.

Builds a SmartNav autoconnect from typed config objects, conditionally
including modules and wiring up remappings based on which features are
provided.

Optional modules are off when ``None`` and on when a config is passed.
Configs are the real module config classes (pydantic models) from each
module — only explicitly-set fields are forwarded to ``.blueprint()``::

    from dimos.navigation.smart_nav.modules.terrain_analysis.terrain_analysis import (
        TerrainAnalysisConfig,
    )
    from dimos.navigation.smart_nav.modules.far_planner.far_planner import FarPlannerConfig
    from dimos.navigation.smart_nav.modules.local_planner.local_planner import (
        LocalPlannerConfig,
    )

    blueprint, rerun_config = make_smart_nav(
        terrain_analysis=TerrainAnalysisConfig(obstacle_height_thre=0.2),
        far_planner=FarPlannerConfig(sensor_range=30.0),
        local_planner=LocalPlannerConfig(autonomy_mode=True, max_speed=1.0),
    )

The returned ``rerun_config`` dict can be passed to ``vis_module()`` or
a ``RerunBridgeModule`` in the caller's own blueprint.

Required external inputs (must be provided by a sensor module in the
caller's autoconnect):

- ``registered_scan`` (``PointCloud2``) — world-frame point cloud.
  ``UnityBridgeModule`` outputs this directly; ``FastLio2`` outputs
  ``lidar`` which must be remapped to ``registered_scan``.
- ``odometry`` (``Odometry``) — vehicle state estimate. Both
  ``UnityBridgeModule`` and ``FastLio2`` output this directly.

Excluded from the output autoconnect (caller provides these):

- Sensor source (``UnityBridgeModule``, ``FastLio2``, etc.)
- Robot effector (``G1HighLevelDdsSdk``, etc.)
- Visualization (``RerunBridgeModule`` / ``RerunWebSocketServer``)
"""

from __future__ import annotations

from typing import Any

from dimos.core.blueprints import Blueprint, autoconnect
from dimos.navigation.smart_nav.blueprints._rerun_helpers import (
    goal_path_override,
    path_override,
    sensor_scan_override,
    static_floor,
    static_robot,
    terrain_map_ext_override,
    terrain_map_override,
    waypoint_override,
)
from dimos.navigation.smart_nav.modules.click_to_goal.click_to_goal import ClickToGoal
from dimos.navigation.smart_nav.modules.cmd_vel_mux import CmdVelMux, CmdVelMuxConfig
from dimos.navigation.smart_nav.modules.local_planner.local_planner import (
    LocalPlanner,
    LocalPlannerConfig,
)
from dimos.navigation.smart_nav.modules.path_follower.path_follower import (
    PathFollower,
    PathFollowerConfig,
)
from dimos.navigation.smart_nav.modules.pgo.pgo import PGO, PGOConfig
from dimos.protocol.pubsub.impl.lcmpubsub import LCM


def _config_kwargs(config: Any) -> dict[str, Any]:
    """Extract explicitly-set fields from a pydantic config as kwargs."""
    return config.model_dump(exclude_unset=True)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Rerun helpers
# ---------------------------------------------------------------------------


def _default_rerun_blueprint() -> Any:
    import rerun.blueprint as rrb

    return rrb.Blueprint(
        rrb.Spatial3DView(origin="world", name="3D"),
    )


def _odometry_tf_override(odom: Any) -> Any:
    """Publish odometry as a TF frame so sensor_scan/path/robot can reference it."""
    import rerun as rr

    tf = rr.Transform3D(
        translation=[odom.x, odom.y, 0.0],
        rotation=rr.Quaternion(
            xyzw=[
                odom.orientation.x,
                odom.orientation.y,
                odom.orientation.z,
                odom.orientation.w,
            ]
        ),
        parent_frame="tf#/map",
        child_frame="tf#/sensor",
    )
    return [("tf#/sensor", tf)]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def make_smart_nav(
    *,
    sensor_scan_generation: Any | None = None,
    terrain_analysis: Any | None = None,
    terrain_map_ext: Any | None = None,
    local_planner: LocalPlannerConfig | None = None,
    path_follower: PathFollowerConfig | None = None,
    pgo: PGOConfig | None = None,
    far_planner: Any | None = None,
    click_to_goal: Any | None = None,
    cmd_vel_mux: CmdVelMuxConfig | None = None,
    rerun_config: dict[str, Any] | None = None,
) -> tuple[Blueprint, dict[str, Any]]:
    """Build a SmartNav autoconnect from per-module configs.

    The returned blueprint expects two external input streams to be
    provided by a sensor module in the caller's autoconnect:

    - ``registered_scan`` (``PointCloud2``) — world-frame point cloud
    - ``odometry`` (``Odometry``) — vehicle state estimate

    The blueprint always publishes:

    - ``cmd_vel`` (``Twist``) — merged velocity (teleop wins over nav)
    - ``corrected_odometry`` (``Odometry``) — loop-closure-corrected pose
    - ``global_map`` (``PointCloud2``) — accumulated keyframe map

    Always-on modules (the minimal functional set):

    - **LocalPlanner** — reactive local obstacle avoidance
    - **PathFollower** — pure-pursuit path → velocity
    - **PGO** — pose graph optimization → corrected odometry + global map
    - **ClickToGoal** — click/goal → waypoint for LocalPlanner
    - **CmdVelMux** — muxes ``tele_cmd_vel`` + ``nav_cmd_vel`` → ``cmd_vel``

    Pass ``None`` to use defaults for these, or a config to customise.
    Always-on configs are imported from this module directly
    (``LocalPlannerConfig``, ``PathFollowerConfig``, ``PGOConfig``,
    ``CmdVelMuxConfig``).

    Optional modules (off when ``None``, on when a config is passed).
    Import the config from its own module:

    - **SensorScanGeneration** — transforms registered_scan to sensor
      frame. Config: ``SensorScanGeneration`` has no config class; pass
      ``True`` to include with defaults.
    - **TerrainAnalysis** — terrain cost map (auto-enables
      ``LocalPlanner.use_terrain_analysis``). Config:
      ``TerrainAnalysisConfig`` from
      ``smart_nav.modules.terrain_analysis.terrain_analysis``.
    - **TerrainMapExt** — extended persistent terrain map (requires
      TerrainAnalysis). Config: ``TerrainMapExtConfig`` from
      ``smart_nav.modules.terrain_map_ext.terrain_map_ext``.
    - **FarPlanner** — global route planner (remaps ClickToGoal's
      waypoint). Config: ``FarPlannerConfig`` from
      ``smart_nav.modules.far_planner.far_planner``.

    A ``rerun_config`` dict can be passed to override or extend the
    generated defaults. Nested dicts (``visual_override``, ``static``)
    are merged — caller entries win. Top-level keys are overridden
    wholesale::

        make_smart_nav(
            terrain_analysis=TerrainAnalysisConfig(obstacle_height_thre=0.2),
            rerun_config={
                "visual_override": {"world/my_stream": my_override_fn},
                "memory_limit": "2GB",
            },
        )

    Returns:
        A ``(blueprint, rerun_config)`` tuple. The blueprint contains the
        SmartNav navigation modules (no effector, no visualization). The
        ``rerun_config`` dict is suitable for passing to ``vis_module()``
        or ``RerunBridgeModule.blueprint()``.
    """
    terrain_enabled = terrain_analysis is not None
    terrain_map_ext_enabled = terrain_enabled and terrain_map_ext is not None
    far_planner_enabled = far_planner is not None
    sensor_scan_enabled = sensor_scan_generation is not None

    modules: list[Blueprint] = []
    remappings: list[tuple[type, str, str]] = []

    # -- Optional modules --------------------------------------------------

    # SensorScanGeneration — no config class; accept any truthy value
    if sensor_scan_enabled:
        from dimos.navigation.smart_nav.modules.sensor_scan_generation.sensor_scan_generation import (
            SensorScanGeneration,
        )

        modules.append(SensorScanGeneration.blueprint())

    # TerrainAnalysis — conditional import based on config class name
    _TerrainAnalysisModule: type | None = None
    if terrain_enabled:
        config_name = type(terrain_analysis).__name__
        if config_name == "TerrainAnalysisConfig":
            from dimos.navigation.smart_nav.modules.terrain_analysis import (
                terrain_analysis as _ta_mod,
            )

            _TerrainAnalysisModule = _ta_mod.TerrainAnalysis
            modules.append(_TerrainAnalysisModule.blueprint(**_config_kwargs(terrain_analysis)))
        else:
            msg = f"Expected TerrainAnalysisConfig, got {config_name}"
            raise TypeError(msg)

    # TerrainMapExt — conditional import based on config class name
    if terrain_map_ext_enabled:
        config_name = type(terrain_map_ext).__name__
        if config_name == "TerrainMapExtConfig":
            from dimos.navigation.smart_nav.modules.terrain_map_ext import (
                terrain_map_ext as _tme_mod,
            )

            modules.append(_tme_mod.TerrainMapExt.blueprint(**_config_kwargs(terrain_map_ext)))
        else:
            msg = f"Expected TerrainMapExtConfig, got {config_name}"
            raise TypeError(msg)

    # FarPlanner — conditional import based on config class name
    if far_planner_enabled:
        config_name = type(far_planner).__name__
        if config_name == "FarPlannerConfig":
            from dimos.navigation.smart_nav.modules.far_planner import (
                far_planner as _fp_mod,
            )

            modules.append(_fp_mod.FarPlanner.blueprint(**_config_kwargs(far_planner)))
            # In route mode, disconnect ClickToGoal's way_point so FarPlanner drives it
            remappings.append((ClickToGoal, "way_point", "_click_way_point_unused"))
        else:
            msg = f"Expected FarPlannerConfig, got {config_name}"
            raise TypeError(msg)

    # -- Always-on modules -------------------------------------------------

    # LocalPlanner — auto-wire use_terrain_analysis
    lp_kwargs = _config_kwargs(local_planner) if local_planner else {}
    if terrain_enabled:
        lp_kwargs.setdefault("use_terrain_analysis", True)
    else:
        lp_kwargs["use_terrain_analysis"] = False
    modules.append(LocalPlanner.blueprint(**lp_kwargs))

    # PathFollower
    pf_kwargs = _config_kwargs(path_follower) if path_follower else {}
    modules.append(PathFollower.blueprint(**pf_kwargs))

    # PGO
    pgo_kwargs = _config_kwargs(pgo) if pgo else {}
    modules.append(PGO.blueprint(**pgo_kwargs))

    # ClickToGoal (no config class — accept any truthy value or None)
    if click_to_goal is not None:
        modules.append(ClickToGoal.blueprint(**_config_kwargs(click_to_goal)))
    else:
        modules.append(ClickToGoal.blueprint())

    # CmdVelMux
    cvm_kwargs = _config_kwargs(cmd_vel_mux) if cmd_vel_mux else {}
    modules.append(CmdVelMux.blueprint(**cvm_kwargs))

    # -- Remappings --------------------------------------------------------

    # PathFollower cmd_vel → CmdVelMux nav input
    remappings.append((PathFollower, "cmd_vel", "nav_cmd_vel"))

    # Global-scale planners use PGO-corrected odometry (per CMU ICRA 2022):
    # local modules (LocalPlanner, PathFollower) stay on raw odometry.
    if far_planner_enabled:
        remappings.append((_fp_mod.FarPlanner, "odometry", "corrected_odometry"))  # type: ignore[possibly-undefined]
    remappings.append((ClickToGoal, "odometry", "corrected_odometry"))
    if terrain_enabled and _TerrainAnalysisModule is not None:
        remappings.append((_TerrainAnalysisModule, "odometry", "corrected_odometry"))

    # -- Build the autoconnect ---------------------------------------------
    result = autoconnect(*modules)
    if remappings:
        result = result.remappings(remappings)

    # -- Build rerun_config ------------------------------------------------
    visual_overrides: dict[str, Any] = {
        "world/odometry": _odometry_tf_override,
        "world/path": path_override,
        "world/way_point": waypoint_override,
        "world/goal_path": goal_path_override,
    }
    if sensor_scan_enabled:
        visual_overrides["world/sensor_scan"] = sensor_scan_override
    if terrain_enabled:
        visual_overrides["world/terrain_map"] = terrain_map_override
        if terrain_map_ext_enabled:
            visual_overrides["world/terrain_map_ext"] = terrain_map_ext_override

    static_defaults: dict[str, Any] = {
        "world/floor": static_floor,
        "world/tf/robot": static_robot,
    }

    base_rerun_config: dict[str, Any] = {
        "blueprint": _default_rerun_blueprint,
        "pubsubs": [LCM()],
        "min_interval_sec": 0.25,
        "visual_override": visual_overrides,
        "static": static_defaults,
        "memory_limit": "1GB",
    }

    # Merge caller overrides: nested dicts are merged (caller wins),
    # top-level keys are replaced wholesale.
    if rerun_config:
        _merge_keys = ("visual_override", "static")
        for key, value in rerun_config.items():
            if key in _merge_keys and isinstance(value, dict):
                base_rerun_config.setdefault(key, {})
                base_rerun_config[key].update(value)
            else:
                base_rerun_config[key] = value

    return result, base_rerun_config
