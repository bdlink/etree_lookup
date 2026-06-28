"""
parsers.py — Discover and parse checksum files in a concert folder.

File selection is based purely on file extension — no filename patterns
or content-based filtering. Any file with a recognised extension is parsed.

Supported formats:
  .md5   —  <hash>  *<filename>   or   <hash>  <filename>
  .ffp   —  <filename>:<hash>  (32-char md5 or 40-char sha1)
  .st5   —  <hash>  *<filename>        (same layout as md5, SHA1-based)

Extension design:
  Add a new parser by defining a function and decorating it with
  @register_parser(".ext", "type_label"). Each parser returns a list of
  (hash_value, filename, hash_type) tuples. No other files need changing.
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Registry: extension -> (hash_type_label, parser_function)
# ---------------------------------------------------------------------------
PARSERS: dict[str, tuple[str, callable]] = {}


def register_parser(extension: str, hash_type: str):
    """Decorator to register a parser for a file extension."""
    def decorator(fn):
        PARSERS[extension.lower()] = (hash_type, fn)
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Audio file filter — only hashes for these extensions are used.
# Filenames containing 'wav' (case-insensitive) are always excluded.
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = {".shn", ".flac"}


def _is_audio_file(filename: str) -> bool:
    """
    Return True if the filename refers to a supported audio file.
    Excludes any filename containing 'wav' (case-insensitive) and any
    extension not in AUDIO_EXTENSIONS.
    """
    lower = filename.lower()
    if "wav" in lower:
        return False
    ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    return ext in AUDIO_EXTENSIONS


# ---------------------------------------------------------------------------
# Individual parsers — selected by extension only, no filename filtering
# ---------------------------------------------------------------------------

@register_parser(".md5", "md5")
def parse_md5(path: Path) -> list[tuple[str, str, str]]:
    """
    Standard md5sum format (any file with .md5 extension):
      <32-hex-chars>  *<filename>
      <32-hex-chars>   <filename>    (space-separated, no asterisk)
    Lines starting with # are comments.
    """
    results = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-fA-F]{32})\s+\*?(.+)$", line)
        if m:
            results.append((m.group(1).lower(), m.group(2).strip(), "md5"))
    return results


@register_parser(".ffp", "ffp")
def parse_ffp(path: Path) -> list[tuple[str, str, str]]:
    """
    FLAC Fingerprint format (any file with .ffp extension).
    No filtering on the filenames inside — extension alone determines parsing.

    Handles all line layouts found in the wild:
      <filename>:<40-hex-hash>       (canonical ffp, SHA1)
      <filename>:<32-hex-hash>       (ffp with md5 hash, seen in older files)
      <32-hex-hash>  *<filename>     (md5-style lines sometimes embedded)
    """
    results = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # FFP filename:hash — accept 32-char md5 or 40-char sha1
        m = re.match(r"^(.+):([0-9a-fA-F]{32}(?:[0-9a-fA-F]{8})?)$", line)
        if m:
            results.append((m.group(2).lower(), m.group(1).strip(), "ffp"))
            continue
        # Embedded md5-style line: <32-hex>  [*]<filename>
        m = re.match(r"^([0-9a-fA-F]{32})\s+\*?(.+)$", line)
        if m:
            results.append((m.group(1).lower(), m.group(2).strip(), "md5"))
    return results


@register_parser(".st5", "st5")
def parse_st5(path: Path) -> list[tuple[str, str, str]]:
    """
    ST5 / shntool fingerprint format. Handles:
      <40-hex-chars>  *<filename>           (standard st5)
      <32-hex-chars>  [shntool]  <filename>  (shntool md5 fingerprint)
    Lines starting with # or ; are comments.
    """
    results = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        # Standard st5: 40-char hash  *filename
        m = re.match(r"^([0-9a-fA-F]{40})\s+\*?(.+)$", line)
        if m:
            results.append((m.group(1).lower(), m.group(2).strip(), "st5"))
            continue
        # Shntool: 32-char hash  [shntool]  filename
        m = re.match(r"^([0-9a-fA-F]{32})\s+\[shntool\]\s+(.+)$", line)
        if m:
            results.append((m.group(1).lower(), m.group(2).strip(), "st5"))
    return results


# ---------------------------------------------------------------------------
# Discovery — purely extension-based, with compound-extension exclusions
# ---------------------------------------------------------------------------

# Compound suffixes to ignore — these are specialised checksum types that
# etreedb does not use for lookup:
#   .tagged.md5  — whole-file md5 of tagged flac (not track-level)
#   .flac.st5    — shntool fingerprints of flac files (st5 format)
#   .shn.st5     — shntool fingerprints of shn files
_IGNORED_SUFFIXES = {".tagged.md5", ".flac.st5", ".shn.st5"}


def _is_ignored_checksum(path: Path) -> bool:
    """Return True if this file has a compound suffix we should skip."""
    name = path.name.lower()
    return any(name.endswith(s) for s in _IGNORED_SUFFIXES)


def find_checksum_files(folder: Path) -> list[Path]:
    """
    Return all checksum files in folder (non-recursive) that are suitable
    for etreedb lookup. Files with ignored compound suffixes are excluded.
    Priority order: ffp > md5 > st5.
    """
    found = []
    for ext in [".ffp", ".md5", ".st5"]:
        found.extend(
            p for p in sorted(folder.glob(f"*{ext}"))
            if not _is_ignored_checksum(p)
        )
    return found


def find_fallback_checksum_files(folder: Path) -> list[Path]:
    """
    Return st5-type checksum files for use as a last resort when no preferred
    files (.md5, .ffp, plain .st5) are found. Includes:
      - *.flac.st5  — shntool fingerprints of flac files
      - *.shn.st5   — shntool fingerprints of shn files
      - *.st5.txt   — txt-wrapped st5 files
    tagged.md5 is excluded even here — whole-file md5 cannot match etreedb.
    """
    found = []
    # flac.st5 and shn.st5 — ignored in normal scan, usable as fallback
    for ext in [".st5"]:
        found.extend(
            p for p in sorted(folder.glob(f"*{ext}"))
            if _is_ignored_checksum(p)
        )
    # txt-wrapped st5 files
    for p in sorted(folder.glob("*.st5.txt")):
        found.append(p)
    return found


def parse_checksum_file(path: Path) -> list[tuple[str, str, str]]:
    """
    Parse a single checksum file, dispatching by extension.
    Returns list of (hash_value, filename, hash_type) for supported audio
    files only (.shn, .flac). Entries referencing .wav or other formats
    are silently excluded.

    Handles compound extensions:
      *.st5.txt  — treated as .st5 (shntool fingerprint wrapped in .txt)

    Raises ValueError if the extension is not recognised.
    """
    name_lower = path.name.lower()

    # Compound extension: *.st5.txt
    if name_lower.endswith(".st5.txt"):
        entries = parse_st5(path)
        return [(h, f, t) for h, f, t in entries if _is_audio_file(f)]

    ext = path.suffix.lower()
    if ext not in PARSERS:
        raise ValueError(f"Unsupported checksum format: {ext!r}")
    _, parser_fn = PARSERS[ext]
    entries = parser_fn(path)
    return [(h, f, t) for h, f, t in entries if _is_audio_file(f)]
