# Developer Tools

Scripts in this folder are for debugging and development only. They are **not** required to run the application.

| Script | Purpose |
|---|---|
| `debug_classes.py` | Print all class IDs that RF-DETR detects on your camera feed. Useful for tuning the `person_mask` filter in `people_counter.py`. |

## Usage

Run from the **project root**:

```bash
python tools/debug_classes.py
```
