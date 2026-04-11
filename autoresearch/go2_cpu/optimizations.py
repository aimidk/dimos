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

"""Optimizations for Go2 blueprint CPU usage.

Monkey-patches applied before replay starts.
Only this file should be modified by the autoresearch agent.

Returns a dict with:
- cli_args: extra CLI flags added before "run"
- env: environment variables to set
- startup_code: Python code written to sitecustomize.py and injected via PYTHONPATH
"""

import os
from pathlib import Path

PATCH_DIR = Path(__file__).parent / "_patches"


def apply() -> dict:
    """Return optimization config for the eval harness."""

    startup_code = """
import dimos.protocol.service.lcmservice as _lcm_mod
import dimos.protocol.rpc.pubsubrpc as _rpc_mod

# 1. Increase LCM polling timeout: 50ms -> 200ms
#    Reduces context switches from ~15k/sec to ~3.75k/sec
_lcm_mod._LCM_LOOP_TIMEOUT = 200

# 2. Reduce RPC thread pool: 50 -> 4 workers per module
#    During replay, RPC calls are minimal
_rpc_mod.PubSubRPCBase._call_thread_pool_max_workers = 4
"""

    # Write sitecustomize.py for injection into subprocess
    PATCH_DIR.mkdir(exist_ok=True)
    (PATCH_DIR / "sitecustomize.py").write_text(startup_code)

    # Prepend patch dir to PYTHONPATH so sitecustomize.py runs on interpreter startup
    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    new_pythonpath = str(PATCH_DIR)
    if existing_pythonpath:
        new_pythonpath = f"{new_pythonpath}:{existing_pythonpath}"

    return {
        # Reduce worker count: 7 is overkill for replay
        "cli_args": ["--n-workers=2"],
        "env": {"PYTHONPATH": new_pythonpath},
        "startup_code": "",  # handled via sitecustomize instead
    }
