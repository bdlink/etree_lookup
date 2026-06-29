"""
torrent_index.py — Build and query a SHNID index from a Torrent folder.

The Torrent folder is expected to have concert folders two levels deep:
    Torrent/
        SpecificFolder/
            gd77-05-08.sbd.5002.sbeok.flac16/
            gd78-12-31.sbd.ashley.1667.sbeok.flac16/

SHNIDs are extracted from concert folder names by scanning period-delimited
tokens for 3-6 digit numeric values.

Index file format (JSON):
    {
        "torrent_dir": "/path/to/Torrent",
        "shnids": [5002, 1667, ...]
    }
"""

import json
import re
from pathlib import Path

INDEX_FILENAME = "torrent_index.json"


def _extract_shnids(folder_name: str) -> set[int]:
    """
    Extract the SHNID from a concert folder name.

    The SHNID is identified as the unique period-delimited token that is
    purely numeric (after stripping leading zeros). If zero or more than
    one such token exists, nothing is returned — ambiguous cases are skipped.

    Tokens that look like years (4 digits, 1900-2099) are excluded.
    Tokens that look like date components (2 digits, part of a YYYY-MM-DD
    sequence within a single token like "gd79-10-28") are not split by
    hyphens — only period delimiters are used.

    Examples:
        gd79-10-28.19590.rolfe.sbeok.flac16    → {19590}
        gd72-08-27.sbd.kaplan-hamilton.152.flac16 → {152}
        gd79-11-02.07920.sbd.macdonald.flac16   → {7920}
        gd77-05-08.maizner.hicks.5002.sbeok.flac16 → {5002}
        (2021) The Stars Were Set in...          → set()
        folder.with.two.123.numeric.456.tokens  → set()
    """
    tokens = folder_name.split(".")
    numeric_tokens: list[int] = []

    for token in tokens:
        if not token:
            continue
        # Must be entirely digits (no letters, hyphens, etc.)
        if not token.isdigit():
            continue
        n = int(token)
        # Must be at least 2 digits
        if len(token) < 2:
            continue
        numeric_tokens.append(n)

    # Exactly one numeric token — unambiguous
    if len(numeric_tokens) == 1:
        return {numeric_tokens[0]}

    # Two tokens: if one is clearly a short sequence number (≤ 3 digits)
    # and the other is longer, use the longer one
    if len(numeric_tokens) == 2:
        a, b = numeric_tokens
        len_a = len(str(a))
        len_b = len(str(b))
        if len_a <= 3 < len_b:
            return {b}
        if len_b <= 3 < len_a:
            return {a}

    # Zero or ambiguous — skip
    return set()


def build_index(torrent_dir: Path, index_path: Path,
                verbose: bool = False) -> set[int]:
    """
    Scan torrent_dir two levels deep, extract SHNIDs from folder names,
    and save the index to index_path.

    Returns the set of SHNIDs found.
    """
    torrent_dir = torrent_dir.expanduser().resolve()
    if not torrent_dir.is_dir():
        raise ValueError(f"Torrent directory not found: {torrent_dir}")

    shnids: set[int] = set()
    folder_count = 0

    for level1 in sorted(torrent_dir.iterdir()):
        if not level1.is_dir():
            continue
        for level2 in sorted(level1.iterdir()):
            if not level2.is_dir():
                continue
            folder_count += 1
            found = _extract_shnids(level2.name)
            if found:
                if verbose:
                    print(f"  {level2.name}: {sorted(found)}")
                shnids |= found
            else:
                print(f"  WARNING: no unique SHNID found in: {level2.name}")

    data = {
        "torrent_dir": str(torrent_dir),
        "shnids": sorted(shnids),
    }
    index_path.write_text(json.dumps(data, indent=2))

    print(f"Indexed {folder_count} folders, found {len(shnids)} SHNIDs.")
    print(f"Index saved to {index_path}")
    return shnids


def load_index(index_path: Path) -> tuple[set[int], str]:
    """
    Load the SHNID index from index_path.

    Returns (shnids_set, torrent_dir_str).
    Raises FileNotFoundError if the index does not exist.
    """
    if not index_path.exists():
        raise FileNotFoundError(
            f"Torrent index not found: {index_path}\n"
            f"Run with --build-index to create it."
        )
    data = json.loads(index_path.read_text())
    shnids = set(data.get("shnids", []))
    torrent_dir = data.get("torrent_dir", "")
    return shnids, torrent_dir


def index_path_default() -> Path:
    """Return the default index path (alongside this module)."""
    return Path(__file__).parent / INDEX_FILENAME
