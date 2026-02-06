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

"""G1 stack with perception and memory."""

from dimos.core.blueprints import autoconnect

from ._perception_and_memory import perception_and_memory
from .unitree_g1_basic import unitree_g1_basic

unitree_g1 = autoconnect(
    unitree_g1_basic,
    perception_and_memory,
).global_config(n_dask_workers=8)

__all__ = ["unitree_g1"]
