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
| `rename.py` | Folder annotation and renaming (with torrent markers) |
| `parsers.py` | Checksum file discovery and parsing |
| `torrent_index.py` | Build and query SHNID index from Torrent folder |
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
| `--depth 1\|2` | `1` | Folder depth for concert scanning. Use `2` for Torrent-style collections where concerts sit inside collection subfolders |
| `--torrent-dir DIR` | — | Check matched SHNIDs against a torrent index; marks matches with `*` |
| `--build-index` | — | Build/rebuild the torrent SHNID index from `--torrent-dir` and exit |
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

## Torrent duplicate checking

Build an index of SHNIDs already in your Torrent collection (run once,
or whenever new torrents are added):

```bash
# First run — provide the torrent folder path
python lookup.py ~/Music/GDCloud --build-index --torrent-dir ~/Music/Torrent

# Subsequent rebuilds — path is remembered in torrent_index.json
python lookup.py ~/Music/GDCloud --build-index
```

Then use `--torrent-dir` during lookup/rename. SHNIDs (or their upgrades)
already in the Torrent folder are marked with `*`:

```bash
# Check a single year folder
python lookup.py ~/Music/GDCloud/77 --precise --rename --torrent-dir ~/Music/Torrent

# Check an entire Torrent collection (two levels deep)
python lookup.py ~/Music/Torrent --precise --depth 2 --torrent-dir ~/Music/Torrent
```

Annotation examples with torrent markers:

| Result | Annotation |
|--------|-----------|
| SHNID in torrent | `gd77-05-08 [5002*]` |
| Upgrade in torrent | `gd72-08-27 [152→2199*]` |
| Both in torrent | `gd72-08-27 [152*→2199*]` |
| Imprecise, in torrent | `gd75-09-28 [2562?*]` |
| Not in torrent | `gd68-03-03 [9374]` (unchanged) |

The `torrent_index.json` file is stored alongside the code and contains
the path to the Torrent folder so `--build-index` can be run without
repeating `--torrent-dir`.

## Extending

**Add a new checksum format**: add `@register_parser(".ext", "type")` in `parsers.py`.

**Add a new output format**: add a writer function + entry to `WRITERS` in `output.py`.

**Swap the lookup backend**: implement `lookup_shnid(checksums, ...)` in a new module
and update the import in `lookup_etree.py`.
