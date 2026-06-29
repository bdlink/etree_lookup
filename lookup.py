#!/usr/bin/env python3
"""
gd_shnid_lookup — Identify Grateful Dead concerts by md5/ffp/st5 checksums.

Usage:
    python lookup.py <folder> [options]

    <folder> must directly contain concert subfolders, e.g.:
        ~/Music/GDCloud/
            gd1977-05-08/
                gd77-05-08.ffp
            gd1978-11-24/
                gd78-11-24.md5

Options:
    --output  csv|json|text     Output format (default: text)
    --out     <file>            Write results to file instead of stdout
    --dry-run                   Parse files only, skip API lookup and renaming
    --rename                    Annotate folder names with SHNID and upgrade chain
    --ffp-only                  Only process folders that contain a .ffp file
    --precise                   Verify matches by comparing etreedb checksum
                                bodies against local hashes
    --verbose / -v              Print per-hash lookup detail
    --delay   SECONDS           Pause between API calls (default: 0.5)
"""

import argparse
import re
import sys
import time
from pathlib import Path

from parsers import (
    find_checksum_files,
    find_fallback_checksum_files,
    parse_checksum_file,
)
from lookup_etree import lookup_shnid
from output import print_results, write_results
from rename import rename_folder
from torrent_index import build_index, load_index, index_path_default


# ---------------------------------------------------------------------------
# Concert scanning
# ---------------------------------------------------------------------------

def scan_concerts(root: Path, verbose: bool = False,
                  ffp_only: bool = False) -> list[dict]:
    """
    Scan each immediate subdirectory of root as one concert folder.

    Preferred checksum files (.ffp, .md5, plain .st5) are used when present.
    Fallback files (.flac.st5, .shn.st5.txt) are used only when no preferred
    files exist. tagged.md5 files are always ignored.

    Returns a list of concert dicts with keys:
        folder, folder_name, checksums, parse_errors, fallback_checksums
    """
    concerts = []

    subdirs = sorted(d for d in root.iterdir() if d.is_dir())
    if not subdirs:
        print(f"WARNING: No subdirectories found in {root}", file=sys.stderr)
        return concerts

    for folder in subdirs:
        checksum_files = find_checksum_files(folder)
        using_fallback = False

        if not checksum_files:
            checksum_files = find_fallback_checksum_files(folder)
            if checksum_files:
                using_fallback = True
                if verbose:
                    names = [f.name for f in checksum_files]
                    print(f"  {folder.name}: using fallback checksums: {names}")
            else:
                if verbose:
                    print(f"  Skipping {folder.name}: no checksum files found")
                continue

        if ffp_only and not any(f.suffix.lower() == ".ffp" for f in checksum_files):
            if verbose:
                print(f"  Skipping {folder.name}: no .ffp file (--ffp-only)")
            continue

        concert: dict = {
            "folder":             folder,
            "folder_name":        folder.name,
            "checksums":          [],
            "parse_errors":       [],
            "fallback_checksums": using_fallback,
        }

        for cf in checksum_files:
            try:
                entries = parse_checksum_file(cf)
                concert["checksums"].extend(entries)
                if verbose:
                    suffix = " (fallback)" if using_fallback else ""
                    print(f"  {folder.name}/{cf.name}: {len(entries)} entries{suffix}")
            except Exception as exc:
                concert["parse_errors"].append(f"{cf.name}: {exc}")

        if concert["checksums"]:
            concerts.append(concert)
        elif verbose:
            print(f"  Skipping {folder.name}: no usable checksum entries parsed")

    return concerts


# ---------------------------------------------------------------------------
# Result initialisation
# ---------------------------------------------------------------------------

def _empty_result(concert: dict) -> dict:
    """Return a result dict pre-filled from concert metadata."""
    return {
        "folder":           str(concert["folder"]),
        "folder_name":      concert["folder_name"],
        "checksums_found":  len(concert["checksums"]),
        "parse_errors":     concert["parse_errors"],
        # Lookup fields (filled in after API call)
        "shnid":            None,
        "shnid_list":       [],
        "ambiguous":        False,
        "ambiguous_upgrades": {},
        "ambiguous_metadata":  {},
        "ambiguous_subset_note": {},
        "ambiguous_identical_note": {},
        "artist":           None,
        "date":             None,
        "venue":            None,
        "city":             None,
        "state":            None,
        "etree_url":        None,
        "matched_hash":     None,
        "matched_hash_type": None,
        "queries_made":     None,
        "st5_only":         False,
        "precise_used":     False,
        "precise_match":    None,
        "precise_failed":   False,
        "precise_missing":  [],
        "precise_extra":    [],
        "upgrades":         [],
        "lookup_error":     None,
        "renamed_to":       None,
    }


def _apply_rename(concert: dict, result: dict, verbose: bool,
                  torrent_shnids: "set[int] | None" = None):
    """Rename the concert folder and update result in place."""
    new_path = rename_folder(concert["folder"], result, verbose=verbose,
                             torrent_shnids=torrent_shnids)
    if new_path:
        result["renamed_to"] = new_path.name
        result["folder"]     = str(new_path)
        concert["folder"]    = new_path   # keep in sync for progress line


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Identify Grateful Dead concerts by checksum → SHNID lookup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "folder",
        help="Folder directly containing concert subfolders",
    )
    parser.add_argument(
        "--output", choices=["text", "csv", "json"], default="text",
        help="Output format (default: text)",
    )
    parser.add_argument("--out", metavar="FILE",
                        help="Write results to file instead of stdout")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse files only; skip API lookup and renaming")
    parser.add_argument("--rename", action="store_true",
                        help="Annotate folder names with SHNID and upgrade chain")
    parser.add_argument("--ffp-only", action="store_true",
                        help="Only process folders that contain a .ffp file")
    parser.add_argument("--precise", action="store_true",
                        help="Verify matches against etreedb checksum bodies")
    parser.add_argument("--errors-only", action="store_true",
                        help="Only output not-found, failed, and ambiguous results")
    parser.add_argument(
        "--torrent-dir", metavar="DIR",
        help="Torrent folder to check for existing SHNIDs (two levels deep)",
    )
    parser.add_argument(
        "--build-index", action="store_true",
        help="Build/rebuild the torrent SHNID index and exit",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--delay", type=float, default=0.5, metavar="SECONDS",
        help="Delay between API calls (default: 0.5s)",
    )
    args = parser.parse_args()

    # Handle --build-index
    index_path = index_path_default()
    if args.build_index:
        torrent_dir = Path(args.torrent_dir).expanduser() if args.torrent_dir else None
        if not torrent_dir:
            # Try loading existing index to get the stored path
            try:
                _, stored_dir = load_index(index_path)
                torrent_dir = Path(stored_dir)
            except FileNotFoundError:
                print("ERROR: --build-index requires --torrent-dir on first run.",
                      file=sys.stderr)
                sys.exit(1)
        build_index(torrent_dir, index_path, verbose=args.verbose)
        sys.exit(0)

    # Load torrent index if available
    torrent_shnids: set[int] | None = None
    if args.torrent_dir:
        try:
            torrent_shnids, _ = load_index(index_path)
            print(f"Torrent index loaded: {len(torrent_shnids)} SHNIDs.",
                  file=sys.stderr)
        except FileNotFoundError as e:
            print(f"WARNING: {e}", file=sys.stderr)

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: '{root}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning: {root}", file=sys.stderr)
    concerts = scan_concerts(root, verbose=args.verbose, ffp_only=args.ffp_only)

    if not concerts:
        print("No concert folders with checksum files found.", file=sys.stderr)
        sys.exit(0)

    print(f"Found {len(concerts)} concert folder(s).", file=sys.stderr)

    results = []
    for i, concert in enumerate(concerts, 1):
        result = _empty_result(concert)

        if args.dry_run:
            result["lookup_error"] = "dry-run: skipped"
        else:
            if args.verbose:
                print(f"\n[{i}/{len(concerts)}] {concert['folder_name']}")
            try:
                match = lookup_shnid(
                    concert["checksums"],
                    verbose=args.verbose,
                    precise=args.precise,
                    inter_query_delay=args.delay,
                )
                if match:
                    result.update(match)
                else:
                    result["lookup_error"] = "not found in etreedb"
            except Exception as exc:
                result["lookup_error"] = str(exc)

            if args.rename:
                _apply_rename(concert, result, verbose=args.verbose,
                              torrent_shnids=torrent_shnids)

            if i < len(concerts):
                time.sleep(args.delay)

        results.append(result)

        # Progress line
        if result.get("ambiguous"):
            status = f"AMBIGUOUS: {', '.join(str(s) for s in result['shnid_list'])}"
        else:
            status = str(result["shnid"] or result["lookup_error"] or "?")
        rename_note = f" → {result['renamed_to']}" if result.get("renamed_to") else ""
        print(f"  [{i}/{len(concerts)}] {concert['folder_name']} → {status}{rename_note}",
              file=sys.stderr)

    if args.out:
        write_results(results, Path(args.out), args.output,
                      errors_only=args.errors_only)
        print(f"\nResults written to {args.out}", file=sys.stderr)
    else:
        print_results(results, args.output, errors_only=args.errors_only)


if __name__ == "__main__":
    main()
