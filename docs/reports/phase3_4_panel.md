# Phases 3 and 4 - placement and panel UI

Run on 2026-11-13, Node v24.18.0, macOS dev machine. **No real Premiere Pro was available.** These tests run the panel's real modules against `panel/test/fake-ppro.js`, a simulated Premiere shaped after docs/PREMIERE_API_NOTES.md that enforces the lock rule (any `create*Action` outside `lockedAccess` throws). They prove the call logic, not Premiere's behaviour; the real gate is the placement self-test on the client's Premiere (docs/CLIENT_SMOKE_TEST.md).

| Gate item (spec section 9) | Evidence | Status |
|---|---|---|
| 3: clips placed in a new sequence, readback zero mismatches | UXP adapter places the 28-take fixture (17 placed) with 0 mismatches; XML adapter imports the Python-written XML and reads back 0 mismatches | PASS in simulation; real Premiere pending client self-test |
| 3: no existing sequence changed | A user sequence created before placement is byte-identical afterwards | PASS in simulation |
| 3: never report success without readback | Fault injection shifts one clip by 1 frame: report.ok = false with one video_start mismatch of 1 frame | PASS |
| 4: every button triggers a real operation | ui.test.js: every <button> in index.html has exactly one handler and each click performs its helper/Premiere call | PASS |
| 4: helper-down and no-audio states shown | api HELPER_DOWN / TOKEN_NOT_FOUND mapped to the red/amber states; NO_AUDIO clips listed as UNSYNCED with reason | PASS (logic); visual check in client test |

```
✔ ticks per frame are exact integers for common rates (0.41425ms)
✔ plan places HIGH/MEDIUM, bins UNSYNCED, keeps LOW for review (0.324209ms)
✔ UXP adapter places every clip and readback matches (9.525583ms)
✔ UXP adapter never relies on undocumented overwrite track creation (6.307542ms)
✔ a misplaced clip is reported, never hidden (3.753ms)
✔ XML adapter: Python-written XML reads back exactly as the JS plan (1.908209ms)
✔ XML adapter: no sequence from importFiles -> manual import path, then verify (1.360417ms)
✔ manual correction moves only that clip; an unsynced clip with an override gets placed (5.133208ms)
✔ readback flags unmuted scratch tracks and unexpected items (0.277ms)
✔ default adapter: only clean self-test reports qualify, fastest wins (0.096666ms)
✔ helper client: token from file, JSON errors, helper down (0.421209ms)
✔ typed positions: timecode, mm:ss and seconds (0.175709ms)
✔ keep scratch audio off: UXP keeps it muted and says so, readback still clean (3.833167ms)
✔ every button in index.html has a click handler (40.011542ms)
✔ each button performs its real operation (1370.8205ms)
✔ review buttons save overrides through the helper and move the draft clip (119.537292ms)
ℹ tests 16
ℹ pass 16
ℹ fail 0
```
