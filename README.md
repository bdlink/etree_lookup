# etree_lookup

Identify Grateful Dead (and related-band) concert recordings by their
checksum files, using the [etreedb.org](https://etreedb.org) GraphQL API.

Looks up md5, ffp, and st5 checksum files in concert folders and matches
them against etreedb sources to find the SHNID (source ID). Supports
precise verification, upgrade chain display, and folder renaming.

## Requirements

- Python 3.9+
- No third-party packages (uses only stdlib: `urllib`, `json`, `csv`, `re`)

## Module structure

| Module | Responsibility |
|--------|---------------|
| `lookup.py` | CLI entry point, concert scanning |
| `lookup_etree.py` | Orchestrates the lookup (probe → resolve → upgrade) |
| `api.py` | GraphQL queries and HTTP transport |
| `resolution.py` | Local hash comparison against etreedb bodies |
| `upgrades.py` | Upgrade chain traversal |
| `rename.py` | Folder annotation and renaming |
| `parsers.py` | Checksum file discovery and parsing |
| `output.py` | Text / CSV / JSON formatters |

## Quick start

```bash
python lookup.py ~/Music/GDCloud
```

Your concerts folder must contain concert subfolders, each holding checksum files:

```
~/Music/GDCloud/
    gd1977-05-08/
        gd77-05-08.ffp
        gd77-05-08.md5
    gd1978-11-24/
        gd78-11-24.md5
    gd1973-06-10/
        gd73-06-10.st5
```

Audio files can be `.shn` or `.flac`. File selection is by extension only
(`.ffp`, `.md5`, `.st5`). Ignored automatically: `tagged.md5`, `flac.st5`,
`shn.st5` — these are used as fallback when no preferred files exist.

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `folder` | *(required)* | Folder directly containing concert subfolders |
| `--output text\|csv\|json` | `text` | Output format |
| `--out FILE` | stdout | Write results to a file |
| `--dry-run` | off | Parse checksums only; skip API calls and renaming |
| `--rename` | off | Annotate folder names with SHNID and upgrade chain |
| `--ffp-only` | off | Only process folders that contain a `.ffp` file |
| `--precise` | off | Verify matches by comparing etreedb checksum bodies |
| `--errors-only` | off | Only output not-found, failed, and ambiguous results |
| `--verbose` / `-v` | off | Show per-hash lookup detail |
| `--delay SECONDS` | `0.5` | Pause between API calls |

## Examples

```bash
# Basic lookup
python lookup.py ~/Music/GDCloud

# Precise verification with errors only
python lookup.py ~/Music/GDCloud --precise --errors-only

# Rename folders with SHNID annotation
python lookup.py ~/Music/GDCloud --precise --rename

# Export to CSV
python lookup.py ~/Music/GDCloud --precise --output csv --out results.csv

# Check a new download folder for duplicates
python lookup.py ~/Music/Torrent/new_downloads --precise --rename --delay 1
```

## Folder renaming

With `--rename`, folders are annotated with the lookup result:

| Result | Example |
|--------|---------|
| Exact match, no upgrades | `gd77-05-08 [12345]` |
| Exact match, with upgrades | `gd77-05-08 [12345→89003→124583]` |
| Imprecise match | `gd77-05-08 [12345?]` |
| Ambiguous | `gd77-05-08 [12345\|67890]` |
| Not found | `gd77-05-08 [not in etreedb]` |

Re-running always strips the old annotation and replaces it.

## Hash type priority

| Local file | etreedb body | Comparison |
|-----------|-------------|-----------|
| `.ffp` | `ffp` | Direct ffp match |
| `.ffp` | `st5` | ffp fingerprints == shntool fingerprints (same audio data) |
| `.md5` / `.shn.md5` | `shn-md5` / `orig-shn-md5` / `flac-md5` | md5 match |
| `.st5` / `.flac.st5` | `st5` | st5 fallback match |

## Precise verification

Without `--precise`, a single hash probe is used (fast, ~2 API calls).
With `--precise`, all candidate checksum bodies are fetched and compared
locally. The result shows:

- `Precise: exact ffp match ✓`
- `Precise: exact md5 match ✓`
- `Precise: exact ffp match ✓ (via etreedb shntool fingerprints)`
- `Precise: FAILED — hashes do not match exactly` + details
- `Precise: unverifiable — no comparable hash type in etreedb`

## Checksums understood

etreedb stores checksum bodies with these descriptions:

| Description | Meaning |
|------------|---------|
| `orig-shn-md5` | Original SHN file md5 checksums |
| `shn-md5` | SHN md5 checksums (may differ from orig) |
| `flac-md5` | FLAC file md5 checksums |
| `ffp` | FLAC fingerprint (filename:hash format) |
| `st5` | Shntool fingerprints (hash [shntool] filename) |
| `d1`, `d2`, … | Per-disc checksums (unioned for comparison) |

## Rate limiting

Default delay is 0.5s between API calls. Increase with `--delay` for large
collections. The tool makes at most 3 API calls per concert (probe +
optional bulk fetch + upgrade chain).

## Extending

**Add a new checksum format**: add `@register_parser(".ext", "type")` in `parsers.py`.

**Add a new output format**: add a writer function + entry to `WRITERS` in `output.py`.

**Swap the lookup backend**: implement `lookup_shnid(checksums, ...)` in a new module
and update the import in `lookup_etree.py`.
