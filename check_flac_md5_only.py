#!/usr/bin/env python3
"""
check_flac_md5_only.py — Diagnostic: find concert folders that would have
NO usable checksum source if plain md5 of .flac files were excluded from
etreedb matching.

Background: plain md5 hashes the whole file (container, tags, padding),
so a .flac and .shn of the same audio produce different md5 values —
unlike shntool fingerprints (.ffp / .st5), which hash audio data only.
etreedb's canonical checksums are shn-based. Comparing local flac-md5
against etreedb bodies can silently misfire (false "extra local tracks",
or a probe hash that never matches). The fix under consideration is to
stop using local flac-referenced md5 hashes for probing/matching
entirely — this script checks how many folders (if any) would be left
with no comparable hash type at all if that happens.

This is READ-ONLY — it does not call the etreedb API and does not modify
any files. It only re-uses the existing local checksum-file discovery and
parsing already in this repo (parsers.py, lookup.py's scan_concerts).

Usage:
    python check_flac_md5_only.py ~/Music/GDCloud --depth 1
    python check_flac_md5_only.py ~/Music/Torrent --depth 2

    Run once per top-level collection (GDCloud is depth 1, Torrent is
    depth 2 — same convention as lookup.py).
"""

import argparse
import sys
from pathlib import Path

# Reuse the exact same folder-discovery and parsing logic lookup.py uses,
# so "what counts as a concert folder" and "what counts as a checksum file"
# stay identical to production behavior.
from lookup import scan_concerts


def classify(concert: dict) -> str:
    """
    Classify a concert's local checksum situation.

    Returns one of:
      "flac-md5-only"   — the ONLY usable hash type is md5 referencing
                           .flac files. Would become unverifiable if
                           flac-md5 is excluded from matching.
      "has-alternative" — has ffp, st5, and/or a shn-referencing md5 —
                           still comparable without flac-md5.
      "no-md5"          — no md5 entries at all (irrelevant to this check).
    """
    checksums = concert["checksums"]  # list of (hash, filename, type)

    has_ffp = any(t == "ffp" for _, _, t in checksums)
    has_st5 = any(t == "st5" for _, _, t in checksums)
    has_shn_md5 = any(
        t == "md5" and f.lower().endswith(".shn") for _, f, t in checksums
    )
    has_flac_md5 = any(
        t == "md5" and f.lower().endswith(".flac") for _, f, t in checksums
    )

    if has_ffp or has_st5 or has_shn_md5:
        return "has-alternative"
    if has_flac_md5:
        return "flac-md5-only"
    return "no-md5"


def main():
    parser = argparse.ArgumentParser(
        description="Find folders that would rely solely on flac-md5 for etreedb lookup."
    )
    parser.add_argument("folder", help="Root folder to scan (same convention as lookup.py)")
    parser.add_argument("--depth", type=int, default=1, choices=[1, 2],
                        help="1 for GDCloud-style (concert = immediate subfolder), "
                             "2 for Torrent-style (concert = 2 levels deep). Default: 1.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print per-folder scan detail (same as lookup.py -v)")
    args = parser.parse_args()

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    concerts = scan_concerts(root, verbose=args.verbose, depth=args.depth)

    flac_only = []
    has_alt = 0
    no_md5 = 0

    for concert in concerts:
        result = classify(concert)
        if result == "flac-md5-only":
            flac_only.append(concert)
        elif result == "has-alternative":
            has_alt += 1
        else:
            no_md5 += 1

    print()
    print("=" * 60)
    print(f"Scanned {len(concerts)} concert folder(s) under {root}")
    print(f"  {has_alt} have an alternative (ffp / st5 / shn-md5) — unaffected")
    print(f"  {no_md5} have no md5 at all — unaffected by this change")
    print(f"  {len(flac_only)} rely SOLELY on flac-referencing md5")
    print("=" * 60)

    if flac_only:
        print()
        print("Folders that would become unverifiable if flac-md5 is excluded:")
        for concert in flac_only:
            md5_files = sorted({
                f for _, f, t in concert["checksums"] if t == "md5"
            })
            print(f"  {concert['folder']}")
            if args.verbose:
                print(f"    referenced flac files (sample): {md5_files[:3]}")
    else:
        print()
        print("None found — safe to exclude flac-md5 from matching with no")
        print("loss of coverage in this collection.")


if __name__ == "__main__":
    main()
