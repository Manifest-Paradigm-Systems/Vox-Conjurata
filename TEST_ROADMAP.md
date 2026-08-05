# Vox Conjurata Test Roadmap & Execution Checklist

This document tracks the execution of unit, integration, and E2E tests across the Vox Conjurata stack.

## Test Checklist

- [x] **Unit Tests**: `pytest services/orchestrator/test_orchestrator_unit.py`
- [x] **E2E Integration Flow**: `python services/orchestrator/test_e2e.py`
- [x] **System Diagnostics**: `python run_diagnostics.py`
- [x] **Project Holodeck Vision Map Scan**: Scanned Otari Landing scene artwork and mapped all 6 grid-positioned tokens.
- [x] **Project Holodeck Token Voice Generation**: Documented and saved voice profiles in `settings/token_voice_mappings.json`.
- [x] **Project Holodeck Voice Hotkey Routing**: Executed `test_hotkey_routing.py` confirming Narrator, Monster, and Player routes.
- [x] **Project Holodeck Cinematic Narration**: Synthesized atmospheric DM narration to `/var/home/EvokeStudio/vox-conjurata/cinematic_scene_narration.mp3`.
- [x] **Workhorse UI v2 Acceptance**: Project tabs + compact layout accepted — `vox-conjurata-webui-v2` campaign PASS (9/9), smoke regression PASS (6/6), unit tests 5/5 (2026-08-04).
