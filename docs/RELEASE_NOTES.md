# Music Video AutoSync 0.1.0 (internal test build)

First build for client testing. Windows 10/11, Premiere Pro 26.2+.

- One installer: local helper (tray app, starts with Windows), sync engine, LGPL FFmpeg, and the
  Premiere UXP panel (`.ccx`).
- Analyses 100-200 takes against one master song; places synced takes in a new sequence; reports
  every take with a status and reason.
- Two placement methods (UXP direct, FCP7 XML import) plus a placement self-test that picks the one
  that works on your Premiere.

Start with `docs/CLIENT_SMOKE_TEST.md`. Known limits: `docs/LIMITATIONS.md`.
This build is not code-signed; confidence thresholds are calibrated on synthetic takes only.
