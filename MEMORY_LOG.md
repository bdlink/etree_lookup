# etree_lookup Project Memory Log
*Generated end of session — for continuity in new conversations*

---

## Project Overview

**Repository**: https://github.com/bdlink/etree_lookup (public)  
**Purpose**: Identify Grateful Dead (and related band) concert recordings by their local checksum files, matching against the etreedb.org GraphQL API to find SHNIDs (source IDs). Supports precise verification, upgrade chain display, folder renaming/annotation, and duplicate detection against a Torrent collection.

**User**: `bdlink` (Brian) — Mac user, runs from terminal, comfortable with Python and git.

---

## Original Problem & Evolution

Started as a v1 tool (`gd_shnid_lookup/`) that had grown organically and accumulated bugs. This session began debugging false negatives in precise matching, then expanded into:

1. Bug fixes in hash resolution logic (the main work)
2. Full refactor from monolithic `lookup_etree.py` into clean module structure (v2)
3. New features: `--errors-only`, ambiguous detail output, subset detection, torrent duplicate checking
4. Ongoing testing and bug fixing against real GDCloud collection

---

## Module Structure (v2 — current)

| Module | Responsibility | Lines |
|--------|---------------|-------|
| `api.py` | GraphQL queries + HTTP transport to etreedb.org | 156 |
| `resolution.py` | Local hash comparison against etreedb bodies | 381 |
| `upgrades.py` | Upgrade chain traversal via source comments | 110 |
| `rename.py` | Folder annotation building + renaming | 124 |
| `parsers.py` | Checksum file discovery and parsing | 214 |
| `lookup_etree.py` | Orchestration: probe → resolve → upgrade | 355 |
| `output.py` | Text/CSV/JSON formatters | 241 |
| `lookup.py` | CLI entry point, concert scanning | 345 |
| `torrent_index.py` | SHNID index from Torrent folder | 143 |

---

## Key Technical Insights

### etreedb API Facts
- GraphQL endpoint: `https://graphql.etreedb.org/`
- Checksum body `description` field values:
  - `orig-shn-md5` — original shn md5s (may be shared across sources)
  - `shn-md5` — shn md5s specific to this entry
  - `flac-md5` — flac md5s
  - `ffp` — flac fingerprints (filename:hash format)
  - `flac-ffp` — same
  - `st5` — shntool fingerprints (hash [shntool] filename format)
  - `d1`, `d2`, `d3`… — per-disc splits (must be unioned)
- Probe query uses `checksums(filter: { body: { contains: $hash } })`
- Bulk fetch uses `sources(filter: { id: { in: $ids } })`
- Upgrade links in `Source.comments` HTML: `<a href="/shninfo_detail.php?shnid=89003">upgrade</a>`

### Critical Hash Insight
**Shntool computes identical fingerprints for .shn and .flac of the same audio** (hashes raw audio data, ignoring container format). Therefore:
- Local `.ffp` hashes (flac fingerprints, `filename:hash` format) == etreedb `st5` body hashes (`hash  [shntool]  filename` format)
- Local `.ffp` hashes also match etreedb `ffp` description bodies directly

### Resolution Logic (`resolution.py`)

`_check_one()` comparison pairs (in priority order):
```python
(local_ffp, cand_ffp, "ffp"),       # explicit ffp body
(local_ffp, cand_st5, "ffp↔st5"),  # shntool fingerprints == flac ffp
(local_md5, cand_md5, "md5"),
(local_st5, cand_st5, "st5"),
```

**Body sort order in `_compare_bodies`**: `shn-md5` is tried BEFORE `orig-shn-md5`. This is critical — `orig-shn-md5` may be shared between multiple sources (e.g. 5649 and 5650 share the same `orig-shn-md5` body), but `shn-md5` represents the actual content of each specific entry.

**`any_comparison_attempted` check**: Uses same priority logic as `_check_one`. If local has ffp but etreedb has no ffp/st5 body, return "probe" (trust initial match) rather than failing.

**`_MatchState.update()`**: Does NOT overwrite real failure detail with empty no-comparison state.

**Disc split union**: When bodies are labelled `d1`, `d2`, etc., they are unioned for comparison if no individual body matched.

**`candidate_hash_sets()`**: Excludes `orig-shn-md5` when computing sets for subset/identical detection. `orig-shn-md5` may have extra filler tracks that would cause false subset relationships (e.g. 225 has filler tracks in `orig-shn-md5` that 5436 doesn't, but their `shn-md5` bodies are identical).

### Ambiguity Resolution

**Subset detection**: After bulk resolve, if candidate A's hash set (excluding `orig-shn-md5`) is a strict subset of candidate B's, A is eliminated. Stored as `ambiguous_subset_note`.

**Identical detection**: If two candidates have equal hash sets, flagged as `ambiguous_identical_note` with "identical audio to X".

**Folder name hints in output**: Folder date and SHNID extracted and compared against each candidate's date/SHNID to show which candidate the folder name suggests.

**Match description tracking**: `MatchDetail.match_description` records which etreedb body description produced the match (e.g. `"orig-shn-md5"`), shown in ambiguous output.

### Real-World Ambiguous Cases Studied

**5649/5650 (gd74-09-21)**:
- Both share identical `orig-shn-md5` (18 tracks including fillers)
- `shn-md5`: 5649 has 12 concert tracks, 5650 has 6 filler tracks (different concert 01/24/69)
- If local has 18 tracks (with fillers): genuinely ambiguous via `orig-shn-md5`
- If local has 12 tracks: resolves to 5649 via `shn-md5` (5650 fails — wrong tracks)
- Output shows: `AMBIGUOUS — matched via orig-shn-md5 (shared original transfer)`

**225/5436 (gd74-10-18)**:
- Both have identical `shn-md5` bodies (same audio, two etreedb entries)
- 225 has extra filler tracks in `orig-shn-md5`, 5436 does not
- Genuinely ambiguous — identical audio
- Output shows: `identical audio to 5436`

**238/22803 (gd76-06-11)**:
- Same concert, same venue, identical audio — requires external info (txt file) to disambiguate

### Checksum File Handling

Priority (preferred):
1. `.ffp` — flac fingerprints
2. `.md5` / `.shn.md5` — md5 checksums
3. `.st5` — shntool fingerprints (plain, not compound)

Ignored normally (but used as fallback when nothing else available):
- `.tagged.md5` — whole-file md5, never usable
- `.flac.st5` — shntool fingerprints of flac files
- `.shn.st5` / `.shn.st5.txt` — shntool fingerprints of shn files

Fallback logic: if a folder has ONLY ignored files (e.g. only `flac.st5`), use them for both probe and precise matching (st5 vs etreedb st5 body).

---

## Folder Annotation Format (`rename.py`)

```
[18120]              exact match, no upgrades
[18120→89003]        exact match, with upgrade chain
[18120?]             imprecise match (precise failed)
[18120?→89003]       imprecise with upgrades
[18120|18121]        ambiguous
[18120→89003|18121]  ambiguous with upgrade chains
[not in etreedb]     not found
```

With torrent markers (`*` = SHNID present in Torrent folder):
```
[18120*]             SHNID in torrent
[152→2199*]          upgrade in torrent
[152*→2199*]         both in torrent
[152?*]              imprecise, in torrent
[225|5436*]          ambiguous, 5436 in torrent
```

Existing annotations are always stripped before applying new one (safe to re-run).
**NEVER use `--rename` on Torrent folders** — breaks torrent seeding.

---

## CLI Reference

```bash
python lookup.py <folder> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--output text\|csv\|json` | text | Output format |
| `--out FILE` | stdout | Write to file |
| `--dry-run` | off | Parse only, no API |
| `--rename` | off | Annotate folder names |
| `--ffp-only` | off | Only .ffp folders |
| `--precise` | off | Verify against etreedb bodies |
| `--errors-only` | off | Only show failures/ambiguous/not-found |
| `--depth 1\|2` | 1 | 2 for Torrent-style (concerts 2 levels deep) |
| `--torrent-dir DIR` | — | Check SHNIDs against torrent index |
| `--build-index` | — | Build torrent index and exit |
| `--verbose / -v` | off | Per-hash detail |
| `--delay SECONDS` | 0.5 | API rate limiting |

### Typical Workflows

```bash
# Scan GDCloud year folder, precise, annotate with torrent markers
python lookup.py ~/Music/GDCloud/77 --precise --rename --torrent-dir ~/Music/Torrent

# Check errors only across whole collection
python lookup.py ~/Music/GDCloud/77 --precise --errors-only

# Scan Torrent folder for unmatched concerts (no rename!)
python lookup.py ~/Music/Torrent --precise --depth 2 --errors-only --delay 1

# Build torrent index (first time)
python lookup.py ~/Music/GDCloud --build-index --torrent-dir ~/Music/Torrent

# Rebuild torrent index (path remembered)
python lookup.py ~/Music/GDCloud --build-index
```

---

## Torrent Index (`torrent_index.py`)

**Structure expected**: `Torrent/CollectionFolder/concertFolder/`

**SHNID extraction from folder name**:
- Split on `.` (periods only — hyphens in dates are never period-delimited)
- Find the unique purely-numeric token
- If exactly one: that's the SHNID
- If two and one is ≤3 digits: take the longer one
- If zero or two both >3 digits: print warning, skip

**Index file**: `torrent_index.json` alongside the code
```json
{
    "torrent_dir": "/path/to/Torrent",
    "shnids": [152, 5002, 19590, ...]
}
```

**Warnings during build**: Any folder where no unique SHNID is found prints a warning (useful for first-run verification).

---

## Test Suite (14 Folders)

Minimum set covering all code branches:

| Folder | Branch |
|--------|--------|
| `66/gd66-01-08.flac16` | not found |
| `66/gd66-07-29.sbd.2243.sbeok.flac16` | exact ffp↔st5 match |
| `68/gd68-03-03.aud.vernon.9374.sbeok.flac16` | exact md5 match |
| `68/gd68-03-31.aud.cotsman.14913.sbeok.flac16` | 3-query probe |
| `68/jg68-07-28.sbd.27968.flac16` | exact ffp match (explicit ffp body) |
| `70/gd70-06-24.aud.lee.5339.sbeok.flac16` | precise failed, missing hashes |
| `torrent/gd70-01-02.18120.early-late.sbd.cotsman.sbeok.flac16` | st5 fallback + exact st5 match |
| `71/nrps71-02-28.sbd.80719.flac16` | disc split (d1/d2 bodies) |
| `72/gd72-08-27.sbd.kaplan-hamilton.152.flac16` | exact md5 + upgrade chain |
| `73/gd73-11-23.sbd.orf.194.sbeok.flac` | precise failed, missing+extra hashes |
| `74/gd74-09-21.sbd.eurodead.5649.sbeok.flac16` | ambiguous, shared orig-shn-md5 |
| `74/gd74-10-18.sbd.romanski.5436.sbeok.flac16` | ambiguous, identical audio |
| `75/gd75-09-28.sbd.unknown.2562.sbefail.flac16` | md5 probe failed, ffp succeeded |
| `76/gd76-06-11.sbd.unknown.22803.sbeok.flac16` | ambiguous, needs external info |

Remaining gap: `--rename` tested manually (run twice — second run tests strip+replace). `--precise unverifiable` not seen in practice.

---

## User Preferences & Working Style

- **Asks for analysis before coding** — expects a plan to be checked before implementation
- **Pushes back on wrong assumptions** — corrects immediately, expects the correction to be internalized
- **Prefers clean design over backward compatibility** — willing to refactor properly
- **Tests on real data** — always runs against actual GDCloud/Torrent folders, reports results
- **"Does this break other things that worked?"** — always asks regression question after fixes
- **Provides raw curl output** — pastes exact API responses to debug
- **Wants explanation of why** — not just what changed but what the root cause was
- **Concise answers preferred** — doesn't want excessive prose
- **git workflow**: downloads files, copies to repo, commits directly to main for now

### Key Corrections Brian Made
1. "The problem is that some folders, st5 should be ignored. But for some, using it is the only possibility" — led to fallback st5 logic
2. "Do not break the typical case to deal with an exceptional case" — about disc splits
3. "Why cannot st5 be used for precise match if it is the only option?" — led to full st5 comparison
4. "In fact, etreedb search does have the ffp hashes for that shnid" — led to ffp↔st5 discovery
5. "The fix works for the test case. It distinguishes between when there is an etreedb ffp file and when we are comparing a local ffp to an etree st5 file" — confirmed ffp vs ffp↔st5 distinction
6. "--rename would break the torrent integrity" — important constraint
7. "I am unclear the need for --torrent-dir here" — when scanning Torrent folder itself

---

## Collaboration Approaches That Worked

1. **Fetch from GitHub before patching** — paste blob URL, Claude fetches, diffs, generates patch
2. **Verbose test runs** — `--verbose` output from real data reveals exact code path taken
3. **Paste curl output** — direct API responses to debug etreedb body structure
4. **Simulate with real hashes** — use actual hash values from folder output in unit tests
5. **"Does X break Y?"** regression checks after each fix

### What Didn't Work
- `git am` patches — the reconstructed "before" state didn't match exactly; direct file replacement is more reliable
- Generating patches without fetching the actual repo file first

---

## Known Issues / Edge Cases

### Resolved
- 2409 (`gd80-11-29`): local `.shn.md5` has 26 tracks, etreedb has 27 — genuine mismatch (missing `f1a83672` = d1t16.shn). Now correctly shows failure with detail.
- 6516 (`gd66-05-19`): now correctly shows `Precise: FAILED` with missing hash detail (local ffp has a track etreedb doesn't)
- 5649/5650: correctly ambiguous when local has fillers; resolves to 5649 when local has 12 clean tracks
- 225/5436: correctly shown as ambiguous with identical audio note

### Open / Under Investigation
- None currently known

---

## Output Format Reference

### Successful match
```
Folder : gd77-05-08.maizner.hicks.5002.sbeok.flac16
SHNID  : 5002
Artist : Grateful Dead
Date   : 05/08/77
Venue  : Barton Hall, Cornell University
City   : Ithaca
State  : NY
URL    : https://etreedb.org/shn/5002
Match  : md5 hash 0b6dce5434a623a6… (2 queries)
Precise: exact md5 match ✓
Upgrades: none
```

### Precise match types
```
Precise: exact md5 match ✓
Precise: exact ffp match ✓
Precise: exact ffp match ✓ (via etreedb shntool fingerprints)
Precise: exact st5 match ✓
Precise: FAILED — hashes do not match exactly
         Local hashes missing from etreedb (1): f1a83672ede3
         Etreedb hashes not in local set (1): d33400604eb8
Precise: unverifiable — no comparable hash type in etreedb
```

### Ambiguous
```
SHNID  : AMBIGUOUS — matched via orig-shn-md5 (shared original transfer)
  Candidate 5649 (folder name match, date matches folder, matched on orig-shn-md5):
    Artist : Grateful Dead
    Date   : 09/21/74
    Venue  : Palais Des Sports
    City   : Paris, France
    URL    : https://etreedb.org/shn/5649
  Candidate 5650 (date 01/24/69 ≠ folder 09/21/74, matched on orig-shn-md5):
    ...
```

### Stats line
```
Results: 22/28 matched, 1 ambiguous, 0 precise-failed, 5 not found
```

---

## Next Steps Identified

1. **Commit current state to repo** — all files are current in sandbox, may need re-download after session
2. **Run full GDCloud scan** with `--torrent-dir` to identify all duplicates
3. **Run Torrent scan** with `--depth 2 --errors-only` to check for any unmatched concerts
4. **Add to test suite**: `--rename` (manual), `--errors-only` flag, non-precise run
5. **Future**: GitHub Actions test suite using the 14-folder test set for regression testing
6. **Future**: Branch strategy for larger changes (currently direct to main)

---

## File Locations

- **Repo**: `~/path/to/etree_lookup/` — all production code
- **GDCloud**: `~/Music/GDCloud/` — organized by decade/year subdirectory
- **Torrent**: `~/Music/Torrent/` — concerts 2 levels deep (CollectionFolder/concertFolder)
- **Torrent index**: `torrent_index.json` alongside the code (in repo root)
- **Test folder**: Subset of GDCloud — 14 folders covering all code branches

---

## GraphQL Query Reference

```bash
# Check descriptions for a SHNID
curl -s -X POST https://graphql.etreedb.org/ \
  -H "Content-Type: application/json" \
  -d '{"query":"{ sources(filter:{id:{eq:SHNID}}) { edges { node { id checksums { edges { node { id description } } } } } } }"}' \
  | python3 -m json.tool

# Check if a hash exists in etreedb
curl -s -X POST https://graphql.etreedb.org/ \
  -H "Content-Type: application/json" \
  -d '{"query":"{ checksums(filter: { body: { contains: \"HASH\" } }) { edges { node { source { id } } } } }"}' \
  | python3 -m json.tool

# Get full body content for multiple SHNIDs
curl -s -X POST https://graphql.etreedb.org/ \
  -H "Content-Type: application/json" \
  -d '{"query":"{ sources(filter:{id:{in:[A,B]}}) { edges { node { id checksums { edges { node { description body } } } } } } }"}' \
  | python3 -m json.tool
```
