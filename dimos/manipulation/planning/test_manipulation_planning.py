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
Unit tests for the Manipulation Planning Module.

Tests the Protocol definitions, data classes, and factory functions
for the manipulation planning stack.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dimos.manipulation.planning.spec import (
    IKResult,
    IKStatus,
    Obstacle,
    ObstacleType,
    PlanningResult,
    PlanningStatus,
    RobotModelConfig,
)

# =============================================================================
# Data Class Tests
# =============================================================================


class TestRobotModelConfig:
    """Tests for RobotModelConfig dataclass."""

    def test_robot_model_config_creation(self):
        """Test basic creation of RobotModelConfig."""
        config = RobotModelConfig(
            name="test_robot",
            urdf_path="/path/to/robot.urdf",
            base_pose=np.eye(4),
            joint_names=["joint1", "joint2", "joint3"],
            end_effector_link="end_effector",
            base_link="base_link",
        )

        assert config.name == "test_robot"
        assert config.urdf_path == "/path/to/robot.urdf"
        assert config.joint_names == ["joint1", "joint2", "joint3"]
        assert config.end_effector_link == "end_effector"
        assert config.base_link == "base_link"

    def test_robot_model_config_defaults(self):
        """Test default values of RobotModelConfig."""
        config = RobotModelConfig(
            name="test",
            urdf_path="/path/to/robot.urdf",
            base_pose=np.eye(4),
            joint_names=["j1"],
            end_effector_link="ee",
        )

        assert config.base_link == "base_link"
        assert config.package_paths == {}
        assert config.joint_limits_lower is None
        assert config.joint_limits_upper is None
        assert config.velocity_limits is None
        assert config.auto_convert_meshes is False
        assert config.xacro_args == {}
        assert config.collision_exclusion_pairs == []

    def test_robot_model_config_with_limits(self):
        """Test RobotModelConfig with joint limits."""
        lower = np.array([-1.0, -2.0, -3.0])
        upper = np.array([1.0, 2.0, 3.0])
        velocity = np.array([1.0, 1.0, 1.0])

        config = RobotModelConfig(
            name="test",
            urdf_path="/path/to/robot.urdf",
            base_pose=np.eye(4),
            joint_names=["j1", "j2", "j3"],
            end_effector_link="ee",
            joint_limits_lower=lower,
            joint_limits_upper=upper,
            velocity_limits=velocity,
        )

        np.testing.assert_array_equal(config.joint_limits_lower, lower)
        np.testing.assert_array_equal(config.joint_limits_upper, upper)
        np.testing.assert_array_equal(config.velocity_limits, velocity)


class TestObstacle:
    """Tests for Obstacle dataclass."""

    def test_box_obstacle(self):
        """Test creating a box obstacle."""
        obstacle = Obstacle(
            name="test_box",
            obstacle_type=ObstacleType.BOX,
            pose=np.eye(4),
            dimensions=(0.1, 0.2, 0.3),
        )

        assert obstacle.name == "test_box"
        assert obstacle.obstacle_type == ObstacleType.BOX
        assert obstacle.dimensions == (0.1, 0.2, 0.3)

    def test_sphere_obstacle(self):
        """Test creating a sphere obstacle."""
        obstacle = Obstacle(
            name="test_sphere",
            obstacle_type=ObstacleType.SPHERE,
            pose=np.eye(4),
            dimensions=(0.05,),
        )

        assert obstacle.obstacle_type == ObstacleType.SPHERE
        assert obstacle.dimensions == (0.05,)

    def test_cylinder_obstacle(self):
        """Test creating a cylinder obstacle."""
        obstacle = Obstacle(
            name="test_cylinder",
            obstacle_type=ObstacleType.CYLINDER,
            pose=np.eye(4),
            dimensions=(0.05, 0.2),
        )

        assert obstacle.obstacle_type == ObstacleType.CYLINDER
        assert obstacle.dimensions == (0.05, 0.2)

    def test_obstacle_with_custom_color(self):
        """Test obstacle with custom color."""
        color = (0.0, 1.0, 0.0, 0.5)
        obstacle = Obstacle(
            name="green_box",
            obstacle_type=ObstacleType.BOX,
            pose=np.eye(4),
            dimensions=(0.1, 0.1, 0.1),
            color=color,
        )

        assert obstacle.color == color

    def test_obstacle_default_color(self):
        """Test obstacle default color is red-ish."""
        obstacle = Obstacle(
            name="default_color",
            obstacle_type=ObstacleType.BOX,
            pose=np.eye(4),
        )

        assert obstacle.color == (0.8, 0.2, 0.2, 0.8)


class TestIKResult:
    """Tests for IKResult dataclass."""

    def test_successful_ik_result(self):
        """Test successful IK result."""
        positions = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        result = IKResult(
            status=IKStatus.SUCCESS,
            joint_positions=positions,
            position_error=0.0001,
            orientation_error=0.001,
            iterations=15,
            message="IK converged",
        )

        assert result.is_success()
        np.testing.assert_array_equal(result.joint_positions, positions)
        assert result.position_error == 0.0001
        assert result.iterations == 15

    def test_failed_ik_result(self):
        """Test failed IK result."""
        result = IKResult(
            status=IKStatus.NO_SOLUTION,
            message="Could not find solution",
        )

        assert not result.is_success()
        assert result.joint_positions is None

    def test_ik_status_values(self):
        """Test all IK status values exist."""
        assert IKStatus.SUCCESS
        assert IKStatus.NO_SOLUTION
        assert IKStatus.SINGULARITY
        assert IKStatus.JOINT_LIMITS
        assert IKStatus.COLLISION
        assert IKStatus.TIMEOUT


class TestPlanningResult:
    """Tests for PlanningResult dataclass."""

    def test_successful_planning_result(self):
        """Test successful planning result."""
        path = [
            np.array([0.0, 0.0, 0.0]),
            np.array([0.1, 0.1, 0.1]),
            np.array([0.2, 0.2, 0.2]),
        ]
        result = PlanningResult(
            status=PlanningStatus.SUCCESS,
            path=path,
            planning_time=0.5,
            path_length=0.52,
            iterations=100,
            message="Path found",
        )

        assert result.is_success()
        assert len(result.path) == 3
        assert result.planning_time == 0.5

    def test_failed_planning_result(self):
        """Test failed planning result."""
        result = PlanningResult(
            status=PlanningStatus.NO_SOLUTION,
            message="No valid path found",
        )

        assert not result.is_success()
        assert result.path == []

    def test_planning_status_values(self):
        """Test all planning status values exist."""
        assert PlanningStatus.SUCCESS
        assert PlanningStatus.NO_SOLUTION
        assert PlanningStatus.TIMEOUT
        assert PlanningStatus.INVALID_START
        assert PlanningStatus.INVALID_GOAL
        assert PlanningStatus.COLLISION_AT_START
        assert PlanningStatus.COLLISION_AT_GOAL


# =============================================================================
# Protocol Conformance Tests
# =============================================================================


class TestProtocolConformance:
    """Tests to verify Protocol definitions are correct."""

    def test_world_spec_is_runtime_checkable(self):
        """Test WorldSpec can be used with isinstance."""
        from dimos.manipulation.planning.spec import WorldSpec

        # Create a mock that has all required methods
        mock_world = MagicMock()
        mock_world.add_robot = MagicMock(return_value="robot_1")
        mock_world.get_robot_ids = MagicMock(return_value=["robot_1"])
        mock_world.finalize = MagicMock()
        mock_world.is_finalized = True

        # Protocol check should work
        assert isinstance(mock_world, WorldSpec)

    def test_kinematics_spec_is_runtime_checkable(self):
        """Test KinematicsSpec can be used with isinstance."""
        from dimos.manipulation.planning.spec import KinematicsSpec

        mock_kin = MagicMock()
        mock_kin.solve = MagicMock()
        mock_kin.solve_iterative = MagicMock()
        mock_kin.solve_differential = MagicMock()

        assert isinstance(mock_kin, KinematicsSpec)

    def test_planner_spec_is_runtime_checkable(self):
        """Test PlannerSpec can be used with isinstance."""
        from dimos.manipulation.planning.spec import PlannerSpec

        mock_planner = MagicMock()
        mock_planner.plan_joint_path = MagicMock()
        mock_planner.get_name = MagicMock(return_value="test_planner")

        assert isinstance(mock_planner, PlannerSpec)


# =============================================================================
# Helper Functions
# =============================================================================


def _drake_available() -> bool:
    """Check if Drake is available."""
    try:
        import pydrake

        return True
    except ImportError:
        return False


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_world_invalid_backend(self):
        """Test create_world raises on invalid backend."""
        from dimos.manipulation.planning.factory import create_world

        with pytest.raises(ValueError, match="Unknown backend"):
            create_world(backend="invalid_backend")

    def test_create_kinematics_invalid_backend(self):
        """Test create_kinematics raises on invalid backend."""
        from dimos.manipulation.planning.factory import create_kinematics

        with pytest.raises(ValueError, match="Unknown backend"):
            create_kinematics(backend="invalid_backend")

    def test_create_planner_invalid_backend(self):
        """Test create_planner raises on invalid backend."""
        from dimos.manipulation.planning.factory import create_planner

        with pytest.raises(ValueError, match="Unknown backend"):
            create_planner(backend="invalid_backend")

    def test_create_planner_invalid_name(self):
        """Test create_planner raises on invalid planner name."""
        from dimos.manipulation.planning.factory import create_planner

        with pytest.raises(ValueError, match="Unknown planner"):
            create_planner(name="invalid_planner", backend="drake")

    def test_create_viz_not_implemented(self):
        """Test create_viz raises NotImplementedError for Drake."""
        from dimos.manipulation.planning.factory import create_viz

        with pytest.raises(NotImplementedError):
            create_viz(backend="drake")


# =============================================================================
# Factory with Drake Backend Tests (requires Drake)
# =============================================================================


@pytest.mark.skipif(
    not _drake_available(),
    reason="Drake not installed",
)
class TestDrakeFactoryIntegration:
    """Integration tests for factory functions with Drake backend."""

    def test_create_world_drake(self):
        """Test creating Drake world."""
        from dimos.manipulation.planning.factory import create_world
        from dimos.manipulation.planning.spec import WorldSpec

        world = create_world(backend="drake", enable_viz=False)
        assert isinstance(world, WorldSpec)
        assert not world.is_finalized

    def test_create_kinematics_drake(self):
        """Test creating Drake kinematics."""
        from dimos.manipulation.planning.factory import create_kinematics
        from dimos.manipulation.planning.spec import KinematicsSpec

        kin = create_kinematics(backend="drake")
        assert isinstance(kin, KinematicsSpec)

    def test_create_planner_rrt_connect(self):
        """Test creating RRT-Connect planner."""
        from dimos.manipulation.planning.factory import create_planner
        from dimos.manipulation.planning.spec import PlannerSpec

        planner = create_planner(name="rrt_connect", backend="drake")
        assert isinstance(planner, PlannerSpec)
        assert planner.get_name() == "RRTConnect"

    def test_create_planner_rrt_star(self):
        """Test creating RRT* planner."""
        from dimos.manipulation.planning.factory import create_planner
        from dimos.manipulation.planning.spec import PlannerSpec

        planner = create_planner(name="rrt_star", backend="drake")
        assert isinstance(planner, PlannerSpec)
        assert planner.get_name() == "RRTStar"


# =============================================================================
# Enum Tests
# =============================================================================


class TestEnums:
    """Tests for enum definitions."""

    def test_obstacle_type_enum(self):
        """Test ObstacleType enum values."""
        assert ObstacleType.BOX
        assert ObstacleType.SPHERE
        assert ObstacleType.CYLINDER
        assert ObstacleType.MESH

    def test_ik_status_enum(self):
        """Test IKStatus enum values."""
        statuses = [
            IKStatus.SUCCESS,
            IKStatus.NO_SOLUTION,
            IKStatus.SINGULARITY,
            IKStatus.JOINT_LIMITS,
            IKStatus.COLLISION,
            IKStatus.TIMEOUT,
        ]
        assert len(statuses) == 6

    def test_planning_status_enum(self):
        """Test PlanningStatus enum values."""
        statuses = [
            PlanningStatus.SUCCESS,
            PlanningStatus.NO_SOLUTION,
            PlanningStatus.TIMEOUT,
            PlanningStatus.INVALID_START,
            PlanningStatus.INVALID_GOAL,
            PlanningStatus.COLLISION_AT_START,
            PlanningStatus.COLLISION_AT_GOAL,
        ]
        assert len(statuses) == 7


# =============================================================================
# Module Export Tests
# =============================================================================


class TestModuleExports:
    """Tests that module exports are correct."""

    def test_planning_module_exports(self):
        """Test that all expected symbols are exported from planning module."""
        from dimos.manipulation.planning import (
            CollisionObjectMessage,
            Detection3D,
            IKResult,
            IKStatus,
            JointTrajectoryGenerator,
            KinematicsSpec,
            Obstacle,
            ObstacleType,
            PlannerSpec,
            PlanningResult,
            PlanningStatus,
            RobotModelConfig,
            VizSpec,
            WorldSpec,
            create_kinematics,
            create_planner,
            create_planning_stack,
            create_world,
        )

        # Verify all exports exist (import would fail otherwise)
        assert IKResult is not None
        assert WorldSpec is not None
        assert create_world is not None
