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

"""
Surveillance data store — lightweight, runs in-process inside WebsocketVisModule.

Receives person sightings from PeopleMonitor and persists them to JSONL.
SurveillanceSkill (a Module in the blueprint) reads from the same files
to answer queries.
"""

from __future__ import annotations

import json
import os
import threading
import time

from dimos.utils.logging_config import setup_logger

logger = setup_logger()

DATA_DIR = os.path.join("assets", "surveillance")
OBS_FILE = os.path.join(DATA_DIR, "observations.jsonl")
ROSTER_FILE = os.path.join(DATA_DIR, "roster.json")

# Throttle: one observation per person per N seconds (unless activity changes)
MIN_OBS_INTERVAL = 5.0

# Max observations to keep in the JSONL file before truncating
MAX_OBSERVATIONS = 2000

# Max people in roster before evicting oldest
MAX_ROSTER = 50


class SurveillanceStore:
    """Persists people activity observations to disk."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._roster: dict[str, dict] = {}
        self._last_obs_ts: dict[str, float] = {}
        self._obs_count = 0
        os.makedirs(DATA_DIR, exist_ok=True)
        self._obs_file = open(OBS_FILE, "a")
        # Count existing observations
        if os.path.exists(OBS_FILE):
            try:
                with open(OBS_FILE) as f:
                    self._obs_count = sum(1 for _ in f)
            except Exception:
                pass
        # Load existing roster
        if os.path.exists(ROSTER_FILE):
            try:
                with open(ROSTER_FILE) as f:
                    self._roster = json.load(f)
                logger.info(f"SurveillanceStore: loaded {len(self._roster)} people from roster")
            except Exception:
                pass

    def on_person_sighting(self, sighting: dict) -> None:
        """Called for each person sighting. Throttles and persists."""
        pid = sighting.get("person_id", "")
        activity = sighting.get("activity", "detected")
        now = time.time()

        with self._lock:
            last_ts = self._last_obs_ts.get(pid, 0.0)
            last_activity = self._roster.get(pid, {}).get("activity", "")

            # Skip if same activity and too recent
            if activity == last_activity and (now - last_ts) < MIN_OBS_INTERVAL:
                return

            self._last_obs_ts[pid] = now
            self._roster[pid] = {
                "person_id": pid,
                "long_term_id": sighting.get("long_term_id"),
                "activity": activity,
                "first_seen": self._roster.get(pid, {}).get("first_seen", now),
                "last_seen": now,
            }

        # Append observation
        obs = {
            "ts": now,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "person_id": pid,
            "long_term_id": sighting.get("long_term_id"),
            "activity": activity,
        }
        try:
            self._obs_file.write(json.dumps(obs) + "\n")
            self._obs_file.flush()
            self._obs_count += 1
        except Exception:
            pass

        # Truncate observation file if it grows too large
        if self._obs_count > MAX_OBSERVATIONS:
            self._truncate_observations()

        # Evict oldest roster entries if over limit
        if len(self._roster) > MAX_ROSTER:
            self._evict_oldest_roster()

        # Save roster periodically (every sighting that passes throttle)
        self._save_roster()

    def _truncate_observations(self) -> None:
        """Keep only the last MAX_OBSERVATIONS/2 entries in the JSONL file."""
        try:
            self._obs_file.close()
            keep = MAX_OBSERVATIONS // 2
            with open(OBS_FILE) as f:
                lines = f.readlines()
            lines = lines[-keep:]
            with open(OBS_FILE, "w") as f:
                f.writelines(lines)
            self._obs_file = open(OBS_FILE, "a")
            self._obs_count = len(lines)
            logger.info(f"SurveillanceStore: truncated observations to {self._obs_count} entries")
        except Exception as e:
            logger.warning(f"SurveillanceStore: truncation failed: {e}")
            self._obs_file = open(OBS_FILE, "a")

    def _evict_oldest_roster(self) -> None:
        """Remove oldest roster entries to stay under MAX_ROSTER."""
        sorted_pids = sorted(
            self._roster.keys(),
            key=lambda pid: self._roster[pid].get("last_seen", 0),
        )
        evict_count = (
            len(self._roster) - MAX_ROSTER + 10
        )  # evict 10 extra to avoid frequent eviction
        for pid in sorted_pids[:evict_count]:
            del self._roster[pid]
            self._last_obs_ts.pop(pid, None)
        logger.info(
            f"SurveillanceStore: evicted {evict_count} oldest roster entries, {len(self._roster)} remaining"
        )

    def _save_roster(self) -> None:
        try:
            with open(ROSTER_FILE, "w") as f:
                json.dump(self._roster, f, indent=2)
        except Exception:
            pass

    def stop(self) -> None:
        if self._obs_file:
            self._obs_file.close()
        self._save_roster()
