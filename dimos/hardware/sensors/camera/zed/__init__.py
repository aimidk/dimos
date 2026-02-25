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

"""ZED camera hardware interfaces."""

from pathlib import Path

from dimos.hardware.sensors.camera.zed.camera import ZEDCamera, ZEDModule, zed_camera
from dimos.msgs.sensor_msgs.CameraInfo import CalibrationProvider

# Set up camera calibration provider (always available)
CALIBRATION_DIR = Path(__file__).parent
CameraInfo = CalibrationProvider(CALIBRATION_DIR)

__all__ = [
    "CameraInfo",
    "ZEDCamera",
    "ZEDModule",
    "zed_camera",
]
