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

"""Compatibility Unitree Go2 blueprint exports.

This module restores the public blueprint names referenced by
``dimos.robot.all_blueprints``. The original monolithic file is no longer
present in this workspace, so these exports map the legacy names to the
currently available modules.
"""

from dimos.core.blueprints import autoconnect
from dimos.protocol.mcp.mcp import MCPModule
from dimos.robot.unitree.connection.go2 import go2_connection
from dimos.robot.unitree_webrtc.keyboard_teleop import keyboard_teleop
from dimos.robot.unitree_webrtc.unitree_skill_container import unitree_skills


basic = autoconnect(go2_connection())
nav = basic
standard = nav
ros = nav
detection = nav
spatial = nav
temporal_memory = nav
vlm_stream_test = nav

agentic = autoconnect(nav, unitree_skills())
agentic_mcp = autoconnect(agentic, MCPModule.blueprint())
agentic_ollama = agentic
agentic_huggingface = agentic

# Retain a direct manual-control variant for local debugging.
manual = autoconnect(nav, keyboard_teleop())


__all__ = [
    "agentic",
    "agentic_huggingface",
    "agentic_mcp",
    "agentic_ollama",
    "basic",
    "detection",
    "manual",
    "nav",
    "ros",
    "spatial",
    "standard",
    "temporal_memory",
    "vlm_stream_test",
]
