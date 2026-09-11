# cerebro-mirror/jarvis — read-only backup (cerebro is canonical)

These files are a **mirror**, copied from cerebro so the Jarvis services survive a
cerebro rebuild and gain version history. **Edit on cerebro, not here**, then re-run the
capture below — this directory is never the source of truth.

| On cerebro | Runs as |
|---|---|
| `/var/home/admin/jarvis/brain/{brain.py,persona.txt}` | `jarvis-brain.service` (:8092) — AG2 as OpenAI models |
| `/var/home/admin/jarvis/panel/panel.py` | `jarvis-docks.service` (:8093) — docks panel + `/live` |
| `/var/home/admin/jarvis/stt-shim/app.py` | `jarvis-stt-shim.service` (:8091) — whisper.cpp → OpenAI STT |
| `/var/home/admin/cerebro-units/container-*.service` | the cerebro system units |

`*.bak-alias` are the pre-`--alias` unit backups — noise kept only because they were in the
captured directory.

## Re-capture

```bash
cd ~/vox-conjurata/cerebro-mirror/jarvis
ssh cerebro-auto 'cd /var/home/admin && tar -cf - jarvis/brain jarvis/panel jarvis/stt-shim cerebro-units' \
  | tar -xf - --strip-components=1
rm -rf */__pycache__
git add -A && git commit -m "mirror: refresh cerebro jarvis sources"
```

The user units also live at `~/.config/systemd/user/jarvis-*.service` on cerebro; the copies
under `brain/`, `panel/` and `stt-shim/` are the install sources.
