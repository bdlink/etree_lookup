"""
rename.py — Build annotation tags and rename concert folders.

Annotation format:
  [18120]               exact match, no upgrades
  [18120→89003]         exact match, with upgrade chain
  [18120?]              imprecise match, no upgrades
  [18120?→89003]        imprecise match, with upgrades
  [18120|18121]         ambiguous, no upgrades
  [18120→89003|18121]   ambiguous, with upgrade chains
  [not in etreedb]      no match found
"""

import re
from pathlib import Path
import sys

# Matches an existing annotation at the end of a folder name, e.g. " [18120→89003]"
_ANNOTATION_RE = re.compile(r"\s*\[.*\]$")


# ---------------------------------------------------------------------------
# Annotation building
# ---------------------------------------------------------------------------

def _chain_str(shnid: str, upgrades: list[dict]) -> str:
    """Build an upgrade chain string like '18120→89003→124583'."""
    parts = [shnid] + [str(u["shnid"]) for u in upgrades]
    return "→".join(parts)


def build_annotation(result: dict) -> str:
    """
    Build the bracketed annotation tag for a lookup result.

    See module docstring for the full format specification.
    """
    # Not found
    if result.get("lookup_error") and not result.get("shnid"):
        return "[not in etreedb]"

    # Ambiguous
    if result.get("ambiguous"):
        shnid_list       = result.get("shnid_list") or []
        ambiguous_upgrades = result.get("ambiguous_upgrades") or {}
        parts = [
            _chain_str(str(s), ambiguous_upgrades.get(str(s), []))
            for s in shnid_list
        ]
        return "[" + "|".join(parts) + "]"

    shnid = result.get("shnid")
    if not shnid:
        return "[not in etreedb]"

    # Determine if match is imprecise
    imprecise = result.get("precise_failed") or (
        result.get("precise_used") and not result.get("precise_match")
    )

    upgrades = result.get("upgrades") or []
    chain = _chain_str(str(shnid), upgrades)

    if imprecise:
        # Insert ? after the matched SHNID, before any upgrade arrow
        if "→" in chain:
            first, rest = chain.split("→", 1)
            chain = f"{first}?→{rest}"
        else:
            chain = f"{chain}?"

    return f"[{chain}]"


# ---------------------------------------------------------------------------
# Folder renaming
# ---------------------------------------------------------------------------

def rename_folder(folder: Path, result: dict,
                  verbose: bool = False) -> "Path | None":
    """
    Rename ``folder`` by stripping any existing annotation and appending
    the annotation built from ``result``.

    Returns the new Path on success, or None if nothing changed or rename failed.
    """
    annotation = build_annotation(result)
    base_name  = _ANNOTATION_RE.sub("", folder.name).rstrip()
    new_name   = f"{base_name} {annotation}"

    if folder.name == new_name:
        if verbose:
            print(f"  Rename skipped: already annotated correctly")
        return None

    new_path = folder.parent / new_name
    try:
        folder.rename(new_path)
        if verbose:
            print(f"  Renamed: {folder.name} → {new_name}")
        return new_path
    except OSError as exc:
        print(f"  WARNING: could not rename '{folder.name}': {exc}",
              file=sys.stderr)
        return None
