# Status — What's Done, What's Next

## Phase 1: Dashboard Shell — DONE

- [x] `mission_control.html` — dark 3-col grid layout
- [x] Routes: `/`, `/mission-control`, `/legacy`, `/command-center`, `/health`, `/api/services`
- [x] SocketIO connection status (green/yellow/red dot in header)
- [x] Auto-launch `dtop` via textual-serve
- [x] Clean shutdown of spy tool subprocesses
- [x] Rerun overlay (shows hint if Rerun not running)

## Phase 2: In-Process Monitors + Claude Chat — DONE

- [x] `AgentMessageMonitor` running in-process, streaming `agent_message` via SocketIO
- [x] `GraphLCMSpy` running in-process, streaming `lcm_stats` every 1s via SocketIO
- [x] Native LCM Stats panel (table with topic name, freq, bandwidth, total)
- [x] MCP Skills panel (polls `localhost:9990/mcp` every 15s, lists all tools)
- [x] Claude Chat panel (`/api/chat` → Claude opus with MCP tool-use, brief tool usage summary)
- [x] Light control skill (`light_skill.py` — Sonoff S31 smart plug via ESPHome)

## Phase 3: Skill Event Stream — DONE

- [x] Real-time `skill_invocation` SocketIO event from both LCM agent path and `/api/chat` HTTP path
- [x] Event payload: `{id, timestamp, name, args, status, result, duration_ms}`
- [x] Duration tracking: matches AIMessage tool_calls to ToolMessage responses by tool_call_id
- [x] Skills Feed panel: live log with timestamp, skill name, args, duration, status badge (RUN/OK/ERR)
- [x] Status updates in-place (running → success/error with duration)
- [x] Orphan tool messages handled gracefully (no crash if start event missed)
- [x] Claude Chat cleaned up: shows "Used: skill_name" instead of dumping raw results/base64
- [x] Tests: 40 tests in `hackathon/tests/test_skills_feed.py`

### Layout changes (Phase 3):
- Removed Command Center (2D map)
- Rerun 3D moved to full left column (rows 1-2)
- LCM Stats + dtop split side-by-side in bottom-left (row 3)
- Skills Feed (col2, row1), Claude (col2, row2), MCP Skills (col2, row3)
- Empty right column (col3, rows 1-3) — reserved for Phase 4

## Phase 4: People Intelligence — DONE

- [x] `PeopleMonitor` — subscribes to Detection2DModule (YOLO 2Hz) via LCM
- [x] Person ReID via OSNet (EmbeddingIDSystem) for persistent IDs across track resets
- [x] Activity classification per person crop via Claude Haiku (every 10s)
- [x] Dashboard panel: person cards in col3 — thumbnail, ID, activity, activity log, "Xs ago"
- [x] SocketIO streaming (`person_sighting` events) with rAF-throttled rendering
- [x] Blob URL management to prevent memory leaks, max 20 cards with LRU eviction
- [x] Bbox area filter (MIN_BBOX_AREA=2000) to reject small false positives
- [x] Tests: 40 tests in `hackathon/tests/test_people_monitor.py`

### Bugs fixed (Phase 4):
- ROS round-trip drops `name` field → filter by `class_id == 0` instead of `d.name == "person"`
- ROS round-trip drops `confidence` (always 0.00) → cannot filter by confidence
- PeopleMonitor logger (`hackathon.people_monitor`) not in DimOS config → use `setup_logger()`
- Image transport mismatch: blueprint uses LCMTransport, PeopleMonitor used pSHMTransport → fixed

## Phase 5: Surveillance Query Engine — DONE

- [x] `SurveillanceStore` — in-process, persists activity observations to `assets/surveillance/`
  - `observations.jsonl` — timestamped activity log (throttled: 1 per 5s or on activity change)
  - `roster.json` — current person states (ID, activity, first/last seen)
- [x] `SurveillanceSkill` — MCP-exposed Module with two skills:
  - `query_surveillance(question)` — answers natural-language questions via Claude Haiku + observation data
  - `list_people()` — returns current roster summary
- [x] Wired into WebsocketVisModule: PeopleMonitor → SurveillanceStore → disk ← SurveillanceSkill
- [x] Blueprint renamed: `unitree-go2-agentic-mcp-surveillance` (dropped temporal_memory)
- [x] API key pattern: `os.getenv("ANTHROPIC_API_KEY")` with explicit error (matches repo pattern)
- [x] Tests: 20 tests in `hackathon/tests/test_surveillance.py`

### Run command:
```bash
dimos --dtop --viewer rerun-web run unitree-go2-agentic-mcp-surveillance
```

## Performance Fixes — DONE

- [x] Throttled crop_b64 regeneration (JPEG encode) to every 5s per person (was every frame)
- [x] Throttled Rerun logging (2D boxes + 3D points) to every 2s (was every frame at 2Hz)
- [x] Throttled person sighting publishing to every 1s per person (was every frame)
- [x] Reduced activity_log cap from 50 to 20 per person
- [x] Downgraded noisy per-frame Rerun warnings to debug level
- [x] SurveillanceStore: auto-truncate observations.jsonl at 2000 entries (keeps last 1000)
- [x] SurveillanceStore: roster eviction when exceeding 50 people (oldest-first)

## UI Enhancements — DONE

- [x] Patrol buttons in header bar (Start/Stop Surveillance via begin_exploration/end_exploration)
- [x] Button state synced with skill_invocation events
- [x] Visual feedback: green active state, PATROLLING indicator, STOP button toggle

## Phase 6: Smarter Activity Classification — TODO

Goal: More accurate activity labels by giving Haiku more context.

- [ ] Send 2-3 sequential crops (buffered over ~5s) instead of a single frame — captures motion/context
- [ ] Include previous activity in prompt so Haiku can note transitions ("was sitting, now standing")
- [ ] Add pose descriptor from Rerun pose detection if available (e.g. "arms raised", "crouching")
- [ ] Tune classify interval (currently 10s) — maybe 15s with multi-crop is better than 10s single

### Files to change:
- `hackathon/people_monitor.py` — buffer recent crops, update `_classify_activity()` prompt

## Phase 7: Alerts & Anomaly Detection — TODO

Goal: Real-time alerts when suspicious or unusual activity is detected. Surveillance-grade awareness.

- [ ] Define alert categories: suspicious (loitering, running, fighting, trespassing), safety (fallen, unresponsive), unusual (unexpected area, after-hours presence)
- [ ] Add anomaly flag to `_classify_activity()` — extend Haiku prompt to return `{"activity": "...", "alert": "none|warning|critical", "reason": "..."}`
- [ ] `AlertSystem` class in people_monitor or new file — tracks alert state per person, deduplicates
- [ ] SocketIO `surveillance_alert` event → dashboard
- [ ] Dashboard: alert banner/toast in header (red flash for critical, yellow for warning)
- [ ] Alert sound (optional, browser notification API)
- [ ] Alert history log — persisted alongside observations.jsonl
- [ ] SurveillanceSkill: new `query_alerts(timeframe)` MCP skill — "any suspicious activity in last 30 min?"
- [ ] Configurable alert rules (e.g. "alert if someone enters zone X after 6pm")

### Files to change:
- `hackathon/people_monitor.py` — extend classify prompt, emit alerts
- `hackathon/surveillance_store.py` — persist alerts
- `hackathon/surveillance_skill.py` — add `query_alerts` skill
- `dimos/web/templates/mission_control.html` — alert banner UI
- New: `hackathon/alert_system.py` (optional, could live in people_monitor)

## Phase 8: Person Search & Quick Actions — TODO

Goal: Click a person card → ask about them. Natural language search for people.

- [ ] "Where is person-3?" / "What has person-2 been doing?" via chat
- [ ] Quick-action buttons on person cards: "Ask about", "Track", "Alert on"
- [ ] "Ask about" sends canned query to `/api/chat`: "Tell me about {person_id}'s recent activity"
- [ ] "Track" highlights person in Rerun 3D view (brighter color, trail)
- [ ] Search bar in People Intelligence panel header — filter/find people by activity or ID
- [ ] Time-range queries: "Who was near the entrance between 2pm and 3pm?"

### Files to change:
- `dimos/web/templates/mission_control.html` — person card buttons, search bar
- `hackathon/surveillance_skill.py` — ensure query_surveillance handles person-specific questions well

## Phase 9: Room Intelligence & Spatial Awareness — TODO (stretch)

Goal: 2D room layout with live person positions, zone-based awareness.

- [ ] 2D room/floor plan overlay — SVG or canvas-based map in command center panel
- [ ] Define zones (rooms, desks, corridors, entrances) with polygon coordinates
- [ ] Map person 3D positions (from odom + bbox projection) onto 2D floor plan
- [ ] Live dots on map showing where each person is
- [ ] Click zone → see who's there and what they're doing
- [ ] Zone-based rules: "alert if zone X has >3 people", "alert if entrance after hours"
- [ ] Desk assignments: label desks with names, show occupancy
- [ ] Heatmap mode: show where people spend the most time over a time window

### Files to change:
- `dimos/web/templates/mission_control.html` — new map panel (replace or augment command center)
- `hackathon/people_monitor.py` — emit zone info with person sightings
- New: `hackathon/room_config.py` — zone definitions, desk assignments
- `hackathon/surveillance_store.py` — persist zone occupancy data
