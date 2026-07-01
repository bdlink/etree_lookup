#!/usr/bin/env python3
"""
inventory_checksum_types.py — Classify every concert folder in a collection
by which checksum-file categories are actually present on disk, independent
of the tool's current preferred/fallback tiering logic.

Why: find_checksum_files() only returns fallback-tier files (.flac.st5,
.shn.st5, .st5.txt) when NO preferred-tier file exists at all. But a folder
can have a *weak* preferred file (flac-referencing .md5, which is container-
format sensitive and matches etreedb only when the tagging happens to be
byte-identical) sitting right next to a *strong* fallback file (.flac.st5,
audio-only like ffp) — and the strong fallback never gets looked at, because
the weak preferred file already satisfied "found >= 1 preferred file".

This script re-classifies every folder from scratch (bypassing that gating)
and buckets it into:

  A. real-preferred    — has .ffp / .ffp.txt / shn-referencing .md5 / plain
                         .st5. Already works correctly today.
  B. HIDDEN-FALLBACK    — no real-preferred file, but DOES have flac-md5
                         AND a fallback-tier file (.flac.st5/.shn.st5/
                         .st5.txt) that's currently being ignored. <-- the bug
  C. flac-md5-only      — no real-preferred, no fallback-tier file, only
                         flac-md5. Would be genuinely unverifiable if
                         flac-md5 is excluded from matching.
  D. fallback-only      — no real-preferred, no flac-md5, but has a
                         fallback-tier file. Already works correctly today
                         (fallback triggers normally since nothing preferred
                         exists).
  E. nothing-usable     — no checksums matched any known category.

This is READ-ONLY — no API calls, no file changes.

Usage:
    python inventory_checksum_types.py ~/Music/Torrent --depth 2
    python inventory_checksum_types.py ~/Music/GDCloud --depth 1
"""

import argparse
import sys
from pathlib import Path

from parsers import parse_checksum_file, _is_ignored_checksum


def _list_concert_dirs(root: Path, depth: int) -> list[Path]:
    if depth == 2:
        dirs = []
        for level1 in sorted(d for d in root.iterdir() if d.is_dir()):
            level2 = sorted(d for d in level1.iterdir() if d.is_dir())
            if level2:
                dirs.extend(level2)
        return dirs
    return sorted(d for d in root.iterdir() if d.is_dir())


def classify_folder(folder: Path) -> dict:
    """Inspect every file in folder and bucket it by checksum category."""
    cats = {
        "ffp": [], "ffp_txt": [],
        "md5_shn": [], "md5_flac": [], "md5_other": [], "md5_tagged": [],
        "st5_plain": [], "st5_flac_fallback": [], "st5_shn_fallback": [],
        "st5_txt_fallback": [],
    }

    for p in sorted(folder.iterdir()):
        if not p.is_file():
            continue
        name = p.name.lower()

        if name.endswith(".ffp.txt"):
            cats["ffp_txt"].append(p)
        elif name.endswith(".ffp"):
            cats["ffp"].append(p)
        elif name.endswith(".st5.txt"):
            cats["st5_txt_fallback"].append(p)
        elif name.endswith(".flac.st5"):
            cats["st5_flac_fallback"].append(p)
        elif name.endswith(".shn.st5"):
            cats["st5_shn_fallback"].append(p)
        elif name.endswith(".st5"):
            cats["st5_plain"].append(p)
        elif name.endswith(".md5"):
            if _is_ignored_checksum(p):
                cats["md5_tagged"].append(p)
            else:
                try:
                    entries = parse_checksum_file(p)
                except Exception:
                    entries = []
                refs_shn = any(f.lower().endswith(".shn") for _, f, _ in entries)
                refs_flac = any(f.lower().endswith(".flac") for _, f, _ in entries)
                if refs_shn:
                    cats["md5_shn"].append(p)
                elif refs_flac:
                    cats["md5_flac"].append(p)
                else:
                    cats["md5_other"].append(p)

    return cats


def bucket(cats: dict) -> str:
    has_real_preferred = bool(
        cats["ffp"] or cats["ffp_txt"] or cats["md5_shn"] or cats["st5_plain"]
    )
    has_flac_md5 = bool(cats["md5_flac"])
    has_fallback = bool(
        cats["st5_flac_fallback"] or cats["st5_shn_fallback"] or cats["st5_txt_fallback"]
    )

    if has_real_preferred:
        return "A"
    if has_flac_md5 and has_fallback:
        return "B"
    if has_flac_md5:
        return "C"
    if has_fallback:
        return "D"
    return "E"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--depth", type=int, default=1, choices=[1, 2])
    parser.add_argument("--show-all", action="store_true",
                        help="Also list folders in buckets A/D/E, not just B/C")
    args = parser.parse_args()

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    dirs = _list_concert_dirs(root, args.depth)
    buckets: dict[str, list] = {"A": [], "B": [], "C": [], "D": [], "E": []}

    for folder in dirs:
        cats = classify_folder(folder)
        if not any(cats.values()):
            continue  # not a concert folder at all (no checksum files) — skip
        b = bucket(cats)
        buckets[b].append((folder, cats))

    total = sum(len(v) for v in buckets.values())
    print()
    print("=" * 70)
    print(f"Scanned {len(dirs)} folder(s) under {root}, {total} had checksum files")
    print(f"  A. real-preferred (ffp/ffp.txt/shn-md5/st5)  : {len(buckets['A'])}  — fine")
    print(f"  B. HIDDEN-FALLBACK (flac-md5 + unused fallback): {len(buckets['B'])}  <-- bug")
    print(f"  C. flac-md5-only, no fallback available       : {len(buckets['C'])}")
    print(f"  D. fallback-only (already works)               : {len(buckets['D'])}  — fine")
    print(f"  E. nothing usable                               : {len(buckets['E'])}")
    print("=" * 70)

    if buckets["B"]:
        print()
        print("Bucket B — HIDDEN FALLBACK available (the bug you found):")
        for folder, cats in buckets["B"]:
            fallback_names = [
                p.name for k in ("st5_flac_fallback", "st5_shn_fallback", "st5_txt_fallback")
                for p in cats[k]
            ]
            print(f"  {folder}")
            print(f"    unused fallback file(s): {fallback_names}")

    if buckets["C"]:
        print()
        print("Bucket C — flac-md5-only, no fallback (would become unverifiable):")
        for folder, _ in buckets["C"]:
            print(f"  {folder}")

    if args.show_all:
        for label, name in [("A", "real-preferred"), ("D", "fallback-only"), ("E", "nothing usable")]:
            if buckets[label]:
                print()
                print(f"Bucket {label} — {name}:")
                for folder, _ in buckets[label]:
                    print(f"  {folder}")


if __name__ == "__main__":
    main()
