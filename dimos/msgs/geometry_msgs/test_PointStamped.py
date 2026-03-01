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

"""Tests for geometry_msgs.Point and geometry_msgs.PointStamped."""

from __future__ import annotations

import time
import unittest

from dimos_lcm.geometry_msgs import Point as LCMPoint

from dimos.msgs.geometry_msgs.PointStamped import Point, PointStamped
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.geometry_msgs.Vector3 import Vector3
from dimos.msgs.protocol import DimosMsg

# ── Point tests ──────────────────────────────────────────────────────────


class TestPoint(unittest.TestCase):
    """Test the Point wrapper."""

    def test_inherits_lcm_point(self):
        p = Point(1.0, 2.0, 3.0)
        self.assertIsInstance(p, LCMPoint)

    def test_coordinates(self):
        p = Point(1.0, 2.0, 3.0)
        self.assertAlmostEqual(p.x, 1.0)
        self.assertAlmostEqual(p.y, 2.0)
        self.assertAlmostEqual(p.z, 3.0)

    def test_default_zero(self):
        p = Point()
        self.assertAlmostEqual(p.x, 0.0)
        self.assertAlmostEqual(p.y, 0.0)
        self.assertAlmostEqual(p.z, 0.0)

    def test_repr(self):
        p = Point(1.0, 2.0, 3.0)
        self.assertIn("Point", repr(p))

    def test_msg_name(self):
        self.assertEqual(Point.msg_name, "geometry_msgs.Point")


# ── PointStamped construction tests ──────────────────────────────────────


class TestConstruction(unittest.TestCase):
    """Test all constructor dispatch variants."""

    def test_from_xyz(self):
        pt = PointStamped(1.0, 2.0, 3.0)
        self.assertAlmostEqual(pt.x, 1.0)
        self.assertAlmostEqual(pt.y, 2.0)
        self.assertAlmostEqual(pt.z, 3.0)

    def test_from_xyz_with_metadata(self):
        pt = PointStamped(1.0, 2.0, 3.0, ts=100.0, frame_id="/world")
        self.assertAlmostEqual(pt.ts, 100.0)
        self.assertEqual(pt.frame_id, "/world")

    def test_from_vector3(self):
        v = Vector3(4.0, 5.0, 6.0)
        pt = PointStamped(v)
        self.assertAlmostEqual(pt.x, 4.0)
        self.assertAlmostEqual(pt.y, 5.0)
        self.assertAlmostEqual(pt.z, 6.0)

    def test_from_list(self):
        pt = PointStamped([7.0, 8.0, 9.0])
        self.assertAlmostEqual(pt.x, 7.0)
        self.assertAlmostEqual(pt.y, 8.0)
        self.assertAlmostEqual(pt.z, 9.0)

    def test_from_tuple(self):
        pt = PointStamped((1.5, 2.5, 3.5))
        self.assertAlmostEqual(pt.x, 1.5)
        self.assertAlmostEqual(pt.y, 2.5)
        self.assertAlmostEqual(pt.z, 3.5)

    def test_default_construction(self):
        pt = PointStamped()
        self.assertAlmostEqual(pt.x, 0.0)
        self.assertAlmostEqual(pt.y, 0.0)
        self.assertAlmostEqual(pt.z, 0.0)

    def test_auto_timestamp(self):
        before = time.time()
        pt = PointStamped(1.0, 2.0, 3.0)
        after = time.time()
        self.assertGreaterEqual(pt.ts, before)
        self.assertLessEqual(pt.ts, after)

    def test_integer_coordinates(self):
        pt = PointStamped(1, 2, 3)
        self.assertAlmostEqual(pt.x, 1.0)
        self.assertIsInstance(pt.x, float)

    def test_inherits_point(self):
        """PointStamped IS a Point IS a LCMPoint."""
        pt = PointStamped(1.0, 2.0, 3.0)
        self.assertIsInstance(pt, Point)
        self.assertIsInstance(pt, LCMPoint)


# ── LCM roundtrip tests ─────────────────────────────────────────────────


class TestLCMRoundtrip(unittest.TestCase):
    """Test LCM binary encode/decode roundtrip."""

    def test_encode_decode_basic(self):
        original = PointStamped(1.5, 2.5, 3.5, ts=1234.5678, frame_id="/world/grid")
        data = original.lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertAlmostEqual(decoded.x, 1.5)
        self.assertAlmostEqual(decoded.y, 2.5)
        self.assertAlmostEqual(decoded.z, 3.5)
        self.assertEqual(decoded.frame_id, "/world/grid")

    def test_timestamp_preserved(self):
        original = PointStamped(0.0, 0.0, 0.0, ts=1709307600.123456789)
        data = original.lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertAlmostEqual(decoded.ts, original.ts, places=6)

    def test_frame_id_preserved(self):
        original = PointStamped(0.0, 0.0, 0.0, frame_id="/robot/base_link")
        data = original.lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertEqual(decoded.frame_id, "/robot/base_link")

    def test_negative_coordinates(self):
        original = PointStamped(-10.5, -20.3, -0.001, frame_id="neg")
        data = original.lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertAlmostEqual(decoded.x, -10.5)
        self.assertAlmostEqual(decoded.y, -20.3)
        self.assertAlmostEqual(decoded.z, -0.001)

    def test_zero_point(self):
        original = PointStamped(0.0, 0.0, 0.0, ts=0.001)
        data = original.lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertAlmostEqual(decoded.x, 0.0)

    def test_lcm_msg_point_assignment(self):
        """Verify lcm_msg.point = self works (Point inherits from LCMPoint)."""
        pt = PointStamped(1.0, 2.0, 3.0)
        lcm_msg = LCMPoint()
        lcm_msg.x = pt.x
        lcm_msg.y = pt.y
        lcm_msg.z = pt.z
        self.assertEqual(pt._get_packed_fingerprint(), LCMPoint._get_packed_fingerprint())


# ── Conversion tests ─────────────────────────────────────────────────────


class TestToPoseStamped(unittest.TestCase):
    def test_position_matches(self):
        pt = PointStamped(1.0, 2.0, 3.0)
        pose = pt.to_pose_stamped()
        self.assertIsInstance(pose, PoseStamped)
        self.assertAlmostEqual(pose.x, 1.0)
        self.assertAlmostEqual(pose.y, 2.0)
        self.assertAlmostEqual(pose.z, 3.0)

    def test_identity_orientation(self):
        pt = PointStamped(1.0, 2.0, 3.0)
        pose = pt.to_pose_stamped()
        self.assertAlmostEqual(pose.orientation.w, 1.0)

    def test_metadata_preserved(self):
        pt = PointStamped(1.0, 2.0, 3.0, ts=500.0, frame_id="/map")
        pose = pt.to_pose_stamped()
        self.assertAlmostEqual(pose.ts, 500.0)
        self.assertEqual(pose.frame_id, "/map")


class TestToRerun(unittest.TestCase):
    def test_returns_points3d(self):
        import rerun as rr

        pt = PointStamped(1.0, 2.0, 3.0)
        archetype = pt.to_rerun()
        self.assertIsInstance(archetype, rr.Points3D)


# ── Protocol tests ───────────────────────────────────────────────────────


class TestDimosMsgProtocol(unittest.TestCase):
    def test_isinstance_check(self):
        pt = PointStamped(1.0, 2.0, 3.0)
        self.assertIsInstance(pt, DimosMsg)

    def test_has_msg_name(self):
        self.assertEqual(PointStamped.msg_name, "geometry_msgs.PointStamped")

    def test_has_lcm_encode(self):
        data = PointStamped(1.0, 2.0, 3.0).lcm_encode()
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)

    def test_has_lcm_decode(self):
        data = PointStamped(1.0, 2.0, 3.0).lcm_encode()
        decoded = PointStamped.lcm_decode(data)
        self.assertAlmostEqual(decoded.x, 1.0)


class TestStringRepresentation(unittest.TestCase):
    def test_str(self):
        s = str(PointStamped(1.0, 2.0, 3.0, frame_id="/world"))
        self.assertIn("1.000", s)
        self.assertIn("/world", s)

    def test_repr(self):
        r = repr(PointStamped(1.0, 2.0, 3.0, frame_id="/world"))
        self.assertIn("PointStamped", r)


if __name__ == "__main__":
    unittest.main()
