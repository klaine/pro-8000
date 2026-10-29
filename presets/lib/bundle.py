#!/usr/bin/env python3
"""Bundles the preset files (presets/*.json) into one bank file for the web editor's "Install the preset bank".

scripts/web.sh writes it next to the editor (tools/web/presets.json, git-ignored) and scripts/pages.sh into the
site's editor/; the release carries it too, as pro8000-presets-<VERSION>.json, for opening in the editor. The bank is
{"format": "pro8000-presets", "version": ..., "presets": [...]}, the presets in file order (P25 before P59, as
install.sh, so a morph target exists before the patch that points at it), each exactly as its file has it.

usage: bundle.py <out.json> [preset directory]
"""
import glob, json, os, sys

if len(sys.argv) not in (2, 3):
    sys.exit(__doc__.strip().splitlines()[-1])
out = sys.argv[1]
here = os.path.dirname(os.path.abspath(__file__))
folder = sys.argv[2] if len(sys.argv) == 3 else os.path.dirname(here)
with open(os.path.join(os.path.dirname(folder), "VERSION")) as f:
    version = f.read().strip()
presets = []
for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
    with open(path) as f:
        p = json.load(f)
    if not isinstance(p, dict) or not isinstance(p.get("slot"), int) or not p.get("name"):
        sys.exit(f"{path}: not a preset (needs a slot and a name)")
    presets.append(p)
with open(out, "w") as f:
    json.dump({"format": "pro8000-presets", "version": version, "presets": presets}, f, separators=(",", ":"))
    f.write("\n")
print(f"{out}: {len(presets)} presets")
