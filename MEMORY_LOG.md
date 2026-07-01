# etree_lookup Project Memory Log
*Generated end of session — for continuity in new conversations*

---

## Project Overview

**Repository**: https://github.com/bdlink/etree_lookup (public)  
**Purpose**: Identify Grateful Dead (and related band) concert recordings by their local checksum files, matching against the etreedb.org GraphQL API to find SHNIDs (source IDs). Supports precise verification, upgrade chain display, folder renaming/annotation, and duplicate detection against a Torrent collection.

**User**: `bdlink` (Brian) — Mac user, runs from terminal, comfortable with Python and git.

---

## Module Structure (v2 — current)

| Module | Responsibility |
|--------|---------------|
| `api.py` | GraphQL queries + HTTP transport to etreedb.org |
| `resolution.py` | Local hash comparison against etreedb bodies |
| `upgrades.py` | Upgrade chain traversal via source comments |
| `rename.py` | Folder annotation building + renaming |
| `parsers.py` | Checksum file discovery and parsing |
| `lookup_etree.py` | Orchestration: probe → resolve → upgrade |
| `output.py` | Text/CSV/JSON formatters |
| `lookup.py` | CLI entry point, concert scanning |
| `torrent_index.py` | SHNID index from Torrent folder |

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
- Upgrade links in `Source.comments` HTML:
  - Standard: `<a href="/shninfo_detail.php?shnid=89003">upgrade</a>`
  - Space variants: `<a href ="/shninfo_detail.php?shnid=2199">upgrade</A>` (space before `=`)
  - Short form: `<a href="/shn/74220">upgrade</a>`
- Compilation/aggregator SHNIDs use placeholder date `??/??/39` — reliable signal for non-concert entries

### Critical Hash Insight
**Shntool computes identical fingerprints for .shn and .flac of the same audio** (hashes raw audio data, ignoring container format). Therefore:
- Local `.ffp` hashes == etreedb `st5` body hashes
- Local `.ffp` hashes also match etreedb `ffp` description bodies directly

### Resolution Logic (`resolution.py`)

`_check_one()` comparison pairs (in priority order):
```python
(local_ffp, cand_ffp, "ffp"),       # explicit ffp body
(local_ffp, cand_st5, "ffp↔st5"),  # shntool fingerprints == flac ffp
(local_md5, cand_md5, "md5"),
(local_st5, cand_st5, "st5"),
```

**`+extra-local` match type**: When etreedb's hashes are all present locally but local has additional hashes not in etreedb (filler tracks). Returns `"{type}+extra-local"` with `missing` = the extra local hashes. Does NOT short-circuit — tries all pairs first, only returns `+extra-local` if no clean exact match is found.

**Body sort order in `_compare_bodies`**: `shn-md5` tried BEFORE `orig-shn-md5`. Critical — `orig-shn-md5` may be shared between sources (e.g. 5649 and 5650 share the same body).

**Probe trust**: When no comparable hash type exists on both sides, returns `MatchDetail("probe", ...)`. This is weak — a compilation with hundreds of hashes may contain any probe hash. Probe-trust candidates are eliminated when any real hash match exists (see below).

**`candidate_hash_sets()`**: Excludes `orig-shn-md5` for subset/identical detection.

**Disc split union**: Bodies labelled `d1`, `d2`, etc. are unioned if no individual body matched.

### Ambiguity Resolution (in order applied)

1. **Probe-trust elimination**: If any survivor has a real hash match (match_type starts with `md5`, `ffp`, or `st5`), all probe-trust-only survivors are eliminated. Catches compilations that pass probe but have no comparable body.

2. **Bogus-date filter**: Candidates with `??` in their date (compilation placeholder `??/??/39`) are eliminated when at least one real-dated candidate exists.

3. **Subset detection**: If candidate A's hash set (excluding `orig-shn-md5`) is a strict subset of B's, B (the superset/compilation) is eliminated. Note: this can fail when hash types are asymmetric across candidates (e.g. 87034 has flac-md5 + ffp; 105772 has only ffp-md5) — bogus-date filter handles that case instead.

4. **Identical detection**: If two candidates have equal hash sets, flagged as "identical audio to X".

5. **Folder name hints**: Date and SHNID from folder name compared against each candidate in ambiguous output.

### Checksum File Handling (`parsers.py`)

**Priority (preferred, `find_checksum_files()`):**
1. `.ffp` / `.ffp.txt` (fingerprint wrapped in `.txt` — same audio-only reliability tier as plain `.ffp`, added this session, see Bug F1 below)
2. `.md5` / `.shn.md5` (excluding tagged variants)
3. `.st5` (plain, not compound)

**Ignored normally, used as fallback (`find_fallback_checksum_files()`):**
- `.tagged.md5` and variants — whole-file md5, never usable for etreedb lookup
- `.flac.st5` — shntool fingerprints of flac files
- `.shn.st5` / `.shn.st5.txt` — shntool fingerprints of shn files

**`_is_ignored_checksum()` logic:**
- Any `.md5` file with `"tag"` in stem → ignored (catches `tagged.md5`, `taggged.md5`, `. tagged.md5`)
- Files ending in `.flac.st5` or `.shn.st5` → ignored (but usable as fallback)
- `.fixed-flac.st5` → NOT ignored (treated as plain st5, used for probe)

**`.shn.st5.txt` files**: Parsed as st5 via compound extension check.

**`.ffp.txt` files**: Parsed as ffp via compound extension check (mirrors `.st5.txt`). Added this session — was previously completely invisible to both `find_checksum_files()` and `find_fallback_checksum_files()`; see Bug F1.

**Plain md5 is container/tag-format sensitive**: unlike shntool fingerprints (`.ffp`/`.st5`, audio-only), plain md5 hashes the whole file, so a `.shn` and `.flac` of the same audio — or two different tagging/encoder passes of the same `.flac` — produce *different* md5 values. This matters a lot; see the flac-md5 investigation section below.

**Silence marker `S`**: Shntool uses `S` instead of a hash for silent tracks. Parser skips these lines silently (no hash extracted).

### Upgrade Chain (`upgrades.py`)

`extract_upgrade_shnid()` regex: `r'href\s*=\s*\"/(?:shninfo_detail\.php\?shnid=|shn/)(\d+)\"[^>]*>\s*upgrade'`
- `\s*=\s*` allows spaces around `=` (e.g. `href ="..."`  or `href= "..."`)
- Case-insensitive to handle `</A>` vs `</a>`
- Matches anchor text starting with "upgrade" (catches "upgrade now in circulation" etc.)

**Known upgrade case**: SHNID 152 → 2199 (uses `href ="..."` with space before `=`)

---

## Real-World Ambiguous Cases Studied

**5649/5650 (gd74-09-21)**:
- Both match via `shn-md5+extra-local` when local has 18 tracks (concert + fillers)
- Genuinely ambiguous — both SHNIDs' shn-md5 bodies are subsets of the local set
- Output shows candidates with date-mismatch hint for 5650 (`date 01/24/69 ≠ folder 09/21/74`)
- Auto-resolution by date not implemented (decided not worth adding)

**225/5436 (gd74-10-18)**:
- Identical `shn-md5` bodies — same audio, two etreedb entries
- Genuinely ambiguous, flagged as "identical audio"

**238/22803 (gd76-06-11)**:
- Same concert, identical audio — requires external info to disambiguate

**87034/105772 (gd72-07-26)**:
- 105772 is a "Jam Of The Week" compilation containing the full 87034 concert
- 105772 passes exact ffp match (its body contains all of 87034's ffp hashes)
- Subset detection fails due to asymmetric hash types (87034 has flac-md5+ffp; 105772 has only ffp-md5)
- **Resolved by bogus-date filter**: 105772 has date `??/??/39`

**16745/147313 (gd77-02-17)**:
- 147313 is "Grateful Dead Compilations" aggregator with `??/??/39` date
- 147313 passes only via probe trust (no comparable hash body)
- **Resolved by probe-trust elimination**

**19418/34874 (gd79-12-05)**:
- 34874 is a composite of 19418 (set1 aud) + 31959 (set2 sbd)
- 34874 passes only via probe trust
- **Resolved by probe-trust elimination**

---

## Known Folder-Specific Notes

**`gd79-12-05.19418.aud.warner.sbeok.t-flac16`**:
- Has `gd79-12-05.19418.taggged.md5` (triple-g typo) — correctly ignored
- Has `gd79-12-05.19418.flac.st5` and `.shn.st5.txt` — used as fallback
- First track (`gd79-12-05audd1t01.flac`) has silence marker `S` in st5 file — skipped

**`gd72-07-25.sbd.cotsman.7046.sbeok.flac16`**:
- Has `gd72-07-25. tagged.md5` (space before "tagged") — correctly ignored
- Has `gd72-07-25.flac.st5` — used as fallback

**`gd73-10-29.sbd.sacks.1014.sbefixed.flac16`**:
- Has `.fixed-flac.st5` (not ignored) and `.tagged.md5` (ignored)
- Fixed audio doesn't match etreedb 1014 — correctly not found

**`gd72-07-26.sbd.GEMS.87034.sbeok.flac16`**:
- Probe returns 87034 + 105772 (JOTW compilation, ??/??/39)
- Both pass exact ffp match — subset detection insufficient
- Resolved by bogus-date filter eliminating 105772

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
Precise: exact md5 match ✓                        ← +extra-local case
Warning: local has 6 track(s) not in etreedb (possible filler):
         a1b2c3d4e5f6
         ...
Precise: FAILED — hashes do not match exactly
         Local hashes missing from etreedb (1): f1a83672ede3
         Etreedb hashes not in local set (1): d33400604eb8
Precise: unverifiable — no comparable hash type in etreedb
```

### Stats line
```
Results: 12/19 matched, 4 ambiguous, 1 precise-failed, 0 extra-local, 2 not found
```

### Folder annotation format
```
[18120]              exact match, no upgrades
[18120→89003]        exact match, with upgrade chain
[18120?]             imprecise match (precise failed)
[18120→2199*]        upgrade in torrent
[225|5436*]          ambiguous, 5436 in torrent
[not in etreedb]     not found
```

---

## Test Suite (21 Folders — all symlinks in `~/Music/GDCloud/test/`)

| Folder | Source | Branch Tested |
|--------|--------|---------------|
| `gd66-01-08.flac16` | GDCloud | not found |
| `d66-07-29.sbd.2243.sbeok.flac16` | GDCloud | exact ffp↔st5 match |
| `gd68-03-03.aud.vernon.9374.sbeok.flac16` | GDCloud | exact md5 match |
| `gd68-03-31.aud.cotsman.14913.sbeok.flac16` | GDCloud | 3-query probe, md5+extra-local |
| `jg68-07-28.sbd.27968.flac16` | GDCloud | exact ffp match (explicit ffp body) |
| `gd70-01-02.18120.early-late.sbd.cotsman.sbeok.flac16` | GDCloud | st5 fallback + exact st5 match |
| `gd70-06-24.aud.lee.5339.sbeok.flac16` | GDCloud | md5+extra-local match |
| `nrps71-02-28.sbd.80719.flac16` | GDCloud | exact md5 match, disc-split union (d1, d2) |
| `gd72-07-25.sbd.cotsman.7046.sbeok.flac16` | Torrent | space-tagged.md5 ignored, flac.st5 fallback |
| `gd72-07-26.sbd.GEMS.87034.sbeok.flac16` | Torrent | bogus-date filter eliminates 105772 |
| `gd72-08-27.sbd.kaplan-hamilton.152.flac16` | GDCloud | exact md5 + upgrade chain (152→2199) |
| `gd73-10-29.sbd.sacks.1014.sbefixed.flac16` | Torrent | fixed-flac.st5 used, not found |
| `gd73-11-23.sbd.orf.194.sbeok.flac16` | GDCloud | precise failed, missing+extra hashes |
| `gd74-09-21.sbd.eurodead.5649.sbeok.flac16` | GDCloud | ambiguous, shn-md5+extra-local both candidates |
| `gd74-10-18.sbd.romanski.5436.sbeok.flac16` | GDCloud | ambiguous, identical audio |
| `gd75-09-28.sbd.unknown.2562.sbefail.flac16` | GDCloud | md5 probe failed, ffp↔st5 succeeded |
| `gd76-06-11.sbd.unknown.22803.sbeok.flac16` | GDCloud | ambiguous, needs external info |
| `gd77-02-17.16745.sbd.outtakes.sbeok.flac16` | Torrent | probe-trust elimination of 147313 |
| `gd79-11-02.124392.mtx.seamons.t-flac16` | Torrent | `.ffp.txt` discovery/parsing (Bug F1) — exact ffp match |
| `gd79-12-05.19418.aud.warner.sbeok.t-flac16` | Torrent | taggged.md5 ignored, flac.st5 fallback, probe-trust elimination of 34874 |
| `gd85-06-14.126338.akg.d'amico.flac16` | Torrent | flac-md5-only, absolute-last-resort path (Bug #11/#12) — after fix: `md5-flac` + `⚠ UNVERIFIED` label |

**Confirmed results (this session, post bugs #9/#10 fixes, 19 folders)**: 13/19 matched, 3 ambiguous, 1 precise-failed, 2 not found. (The old "12/19, 4 ambiguous" figure predates this log — it was already stale before this session, written before probe-trust elimination and the bogus-date filter were finished. `87034`, `16745`, and `19418` correctly resolve to single matches instead of staying ambiguous against their compilation/composite counterparts.)

**21-folder run (after adding `124392` and `126338`)**: 15/21 matched, 3 ambiguous, 1 precise-failed, 1 extra-local, 2 not found. `124392` confirmed `.ffp.txt` discovery working (exact ffp match). `126338` initially exposed Bug #12 (see above) — after the fix, **confirmed via live re-run**: `SHNID 126338: exact md5-flac match ✓` in verbose output, and the final report correctly shows `Precise: whole-file md5 match ✓  ⚠ UNVERIFIED — flac/tag-dependent, not an audio match`. Bug #12 fix is fully validated against real data — no further action needed here.

**Gaps**: `--rename` still only tested manually, not through the formal suite. `--precise unverifiable` still not observed in real data — code path may be unreachable; worth a synthetic test. ~~`14913`/`5339`/`80719`... need a fresh run~~ **Confirmed via live `--precise --verbose` re-run**: `14913` now probes via the correct shn-referenced hash and resolves as a clean `exact md5 match` (no false extra-local); `5339` correctly shows the `+extra-local` warning for a genuine filler track; `80719` shows `Trying disc-split union (d1, d2)…` actually running and resolves cleanly. Every md5 probe in the run referenced a `.shn` file, confirming shn-md5 consistently wins the probe slot over flac-md5. No `md5-flac`/`⚠ UNVERIFIED` output seen in the 19-folder run (expected — none of those are flac-md5-only). See Bug #12 above for what happened once a real flac-md5-only folder (`126338`) was added.

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

# Scan Torrent collection folder (depth 2)
python lookup.py ~/Music/Torrent/"Collection Name" --precise --errors-only --depth 2 --delay 1

# Single parent folder (scan all concerts inside)
python lookup.py ~/Music/Torrent/"Grateful Dead Project 1979 Part 9 = 19.0 GB" --precise --verbose
```

**Note**: Cannot pass a single concert folder directly — the tool looks for subdirectories inside the given path. Pass the parent folder instead.

**Deferred**: Recursive folder scanning to replace `--depth 1|2`. Any folder containing a checksum file would be treated as a concert. "Shallowest folder wins" for nested checksums, with a warning. Implement as test script first before replacing production code.

---

## SHNID Extraction (torrent_index.py)

From folder name:
- Find the unique purely-numeric token
- If exactly one: that's the SHNID
- If two and one is ≤3 digits: take the longer one
- If zero or two both >3 digits: print warning, skip

**Index file**: `torrent_index.json` alongside the code

---

## Bugs Fixed (This Session)

### 1. Tagged md5 typo variants ignored incorrectly (`parsers.py`)
**Problem**: `_is_ignored_checksum()` checked exact suffix `.tagged.md5`. Files named `taggged.md5` (triple-g) or `. tagged.md5` (space before word) were not caught, so they were used as regular md5 files — producing whole-file hashes that never match etreedb.

**Fix**: Changed to check `"tag" in name[:-4]` for any `.md5` file. Catches any spelling variant.

**Affected folders**: `gd79-12-05.19418` (taggged.md5), `gd72-07-25.7046` (. tagged.md5)

### 2. Probe-trust candidates surviving alongside real hash matches (`lookup_etree.py`)
**Problem**: Compilation SHNIDs (105772, 147313) and composite SHNID (34874) were passing resolution via probe trust — they contain the probe hash somewhere in their large body, but have no comparable hash type for full verification. They appeared as spurious ambiguous candidates.

**Fix**: After `precise_survivors` built, if any survivor has a real hash match (match_type starts with `md5`, `ffp`, or `st5`), eliminate all probe-trust-only survivors.

### 3. Bogus-date filter for compilation SHNIDs (`lookup_etree.py`)
**Problem**: 105772 (JOTW compilation) passed with `exact ffp match` — its body genuinely contains all local ffp hashes. Probe-trust elimination doesn't help. Subset detection fails due to asymmetric hash types across candidates.

**Fix**: After survivors built, eliminate candidates with `??` in their date when real-dated candidates exist. `??/??/39` is etreedb's placeholder for compilation/aggregator entries.

### 4. Subset detection direction reversed (`lookup_etree.py`)
**Problem**: When A ⊂ B, code eliminated A (the specific concert) and kept B (the superset/compilation). Should be the reverse.

**Fix**: Changed `to_remove.add(a)` → `to_remove.add(b)`. Updated comment.

### 5. `+extra-local` false positives from short-circuit (`resolution.py`)
**Problem**: `_check_one()` returned `+extra-local` immediately on first matching pair, even if a later pair would give a clean exact match (e.g. ffp↔st5 triggered extra-local, then md5 would have been exact).

**Fix**: Deferred `+extra-local` — try all pairs first, only return extra-local if no exact match found across any pair.

### 6. `Warning: local has 0 track(s)` printed spuriously (`output.py`)
**Fix**: Added guard — only print warning when `extra_local` count > 0.

### 7. Upgrade regex missed `href =` with space before `=` (`upgrades.py`)
**Problem**: SHNID 152 comments contain `href ="..."` (space before `=`). Regex `href=\s*"` allowed space after `=` but not before.

**Fix**: Changed to `href\s*=\s*"`. SHNID 152 now correctly shows upgrade to 2199.

### 8. `+extra-local` new match type added (`resolution.py`, `lookup_etree.py`, `output.py`)
**Feature**: When etreedb's hashes are all present locally but local has additional tracks, report as soft warning rather than FAILED. Shown as `Precise: exact md5 match ✓` + `Warning: local has N track(s) not in etreedb`. Included in `--errors-only` output. Stats line shows `extra-local` count separately.

---

## New Session: flac-md5 Investigation

Triggered by running the 19-folder test suite: found the `+extra-local` warning (bug #8, above) was never actually printing, which led to discovering that plain md5 of `.flac` files is fundamentally unreliable for etreedb matching. What started as one bug turned into a multi-part investigation. Status of each piece below — **some fixes are written but NOT yet applied**, tracked explicitly since patch delivery mistakes happened this session (see Collaboration Notes).

### Confirmed APPLIED this session

**Bug #9 — `+extra-local` warning never printed (`resolution.py`)**
`_compare_bodies()` built the final `MatchDetail` with `missing` hardcoded to `[]` at both return sites, even when the match was `+extra-local` and `result.missing` held the real extra-hash list. Since `_MatchState.matched` only checks `match_type is not None`, the extra-local result still returned successfully — just with its `missing` list silently discarded before it ever reached `output.py`. Fixed: both return sites now pass `result.missing` through.

**Bug #10 — disc-split false positive (`resolution.py`)**
`_compare_bodies()` Pass 1 checked each etreedb body individually, including `d1`/`d2` disc-split bodies one at a time, *before* Pass 2's proper union logic ever ran. A lone `d1` body naturally looks like local has "extra" tracks (the `d2` tracks), which tripped `+extra-local` and returned immediately — so multi-disc candidates never reached the correct union comparison. Fixed: disc-labelled bodies are now skipped in Pass 1 (deferred entirely to Pass 2) whenever more than one distinct disc description exists. Guarded so a genuinely lone `d1`-only body (no `d2` etc.) still gets checked normally in Pass 1 — otherwise it would never be tested at all.

**Bug F1 — `.ffp.txt` files completely invisible to discovery (`parsers.py`)**
Neither `find_checksum_files()` nor `find_fallback_checksum_files()` recognized `*.ffp.txt` — not even as a fallback. Folders using this naming convention (confirmed real-world example: `gd79-11-02.mtx.seamons.fingerprint.ffp.txt`) had their best available checksum source silently skipped entirely. Fixed: `.ffp.txt` is now discovered and parsed identically to plain `.ffp` (same reliability tier, not a fallback — it's the same audio-only format, just text-wrapped), mirroring how `.st5.txt` was already handled. Confirmed working: re-running `check_flac_md5_only.py` on the full Torrent collection dropped the "flac-md5-only" folder count from 41 to 28 (13 folders recovered a real `.ffp`/`.ffp.txt` source).

### Also confirmed applied this session (Bug #11 and the flac-md5 last-resort design)

**Bug #11 — flac-md5 and shn-md5 conflated into one local hash set (`resolution.py`, `lookup_etree.py`)**
Root cause of the original `14913` false `+extra-local`: a folder can ship both a shn-referencing `.md5` and a flac-referencing `.md5` (real example: `gd68-03-31.shn.md5` + `gd68-03-31.aud.14913.md5`, confirmed via `cat` — completely different hash values for the same 3 tracks, e.g. `45594474...` vs `02719149...`). `parsers.py` tags both as the generic type `"md5"` with no format distinction, so they get unioned into one flat `local_md5` set. Comparing that against a single-format etreedb body (e.g. `shn-md5`) makes the flac-md5 hashes look like "extra local tracks" that aren't real.

Designed fix: `_split_md5_by_format()` (new, in `lookup_etree.py`) splits local md5 hashes by referenced audio format (`.shn` vs `.flac`). `resolve()`/`_compare_bodies()`/`_check_one()` (resolution.py) now accept optional `local_md5_shn`/`local_md5_flac` and use whichever matches a given etreedb body's declared format, instead of the flat union. Verified against real `14913` hash values with a simulated etreedb response — reproduces the bug without the fix, resolves cleanly (`exact md5 match`, no extra) with it.

**Status: CONFIRMED APPLIED.** First delivery of this fix (`md5_format_split_fix.patch`) was incomplete — it only captured the `lookup_etree.py` half via a `git diff` mistake, missing the `resolution.py` half entirely. User correctly never applied that broken patch. Superseded by full-file replacements of `resolution.py` / `lookup_etree.py` / `output.py`, which the user downloaded and committed — this fix is live.

### Design decision: flac-md5 as absolute last resort

Considered fully excluding flac-md5 from matching (plain md5 is container/tag-format sensitive — a match only confirms "byte-identical to a specific tagged release," not audio identity, unlike `.ffp`/`.st5` which hash audio data only). Quantified the real-world impact before deciding, using new diagnostic scripts (see below) against the full `~/Music/Torrent` collection (5589 folders):

| Bucket | Count | Meaning |
|--------|-------|---------|
| A. real-preferred (ffp/ffp.txt/shn-md5/st5) | 4274 | unaffected either way |
| D. fallback-only (already works) | 1268 | unaffected either way |
| B. **hidden-fallback bug** | 9 | has flac-md5 *and* an unused `.flac.st5`/`.shn.st5`/`.st5.txt` fallback — see Bug F2 below |
| C. flac-md5-only, no fallback at all | 19 | would go from "some signal" to "nothing" under full exclusion |
| E. nothing usable | 2 | out of scope |

Also ran the live lookup against the 41 (later 28, after Bug F1 fix) flac-md5-only-classified folders (`check_flac_md5_folder_status.py`) to see what flac-md5 matches currently look like: most "succeeded" with a bare `md5` label indistinguishable from a real match, one showed a genuine `+extra-local`, and a comment on a matched SHNID (`105530`, matched from folder `gd79-10-28.150530...`) explicitly said *"this is a tagged version of"* another SHNID — direct confirmation that flac-md5 matches can resolve to a derivative/tagged etreedb entry rather than the canonical one. ~29% of the probed folders came back `NOT FOUND` despite (presumably) being real concerts — likely false negatives from tagging/encoder differences versus whatever etreedb hashed.

**Decision**: keep flac-md5, but as absolute last resort only, with output that can never be confused with a real match.

Designed fix (in `resolution.py` / `lookup_etree.py` / `output.py`):
- Probe order fixed: shn-md5 (or unrecognized-format md5) → ffp → st5 → flac-md5 tried last. Previously any md5 hash — shn- or flac-referenced — could win the probe slot arbitrarily, since both were lumped as type `"md5"`.
- Body-check order in `_compare_bodies()` fixed: `flac-md5` moved to strictly last position (previously it was tried *second*, right after `shn-md5` — backwards for a last-resort format).
- New distinct match_type label `"md5-flac"` (and `"md5-flac+extra-local"`) — never just `"md5"` — so it can't silently masquerade as a real audio-verified match. `output.py` prints it as: `Precise: whole-file md5 match ✓  ⚠ UNVERIFIED — flac/tag-dependent, not an audio match`.
- Verified via synthetic tests: a flac-md5-only candidate still resolves (as `md5-flac`); a candidate with both `shn-md5` and `flac-md5` bodies available always prefers the real `shn-md5` match regardless of body order in the API response.

**Status: CONFIRMED APPLIED** — same delivery (full-file replacement of `resolution.py`, `lookup_etree.py`, `output.py`), downloaded and committed by the user. Confirmed via live `--precise --verbose` re-run on the (now 19-folder) test suite: all cases behaved correctly, but see Bug #12 below — the *labeling itself* had a real bug, caught by expanding the test suite exactly as intended.

### Bug #12 — flac-md5 label relied on etreedb's description string, which isn't reliably named (`resolution.py`)

**How it was found**: user's own instinct to add real-world Torrent examples to the test suite before calling this session's work done — added `gd85-06-14.126338` (a confirmed bucket-C flac-md5-only folder) specifically to exercise the new last-resort path. It resolved as a plain `Precise: exact md5 match ✓` with no `⚠ UNVERIFIED` warning at all — silently defeating the entire point of the last-resort design.

**Root cause**: the Bug #11 / last-resort implementation chose which local set to compare (`local_md5_shn` vs `local_md5_flac`) — and therefore which label to report — by checking whether the etreedb body's `description` field was literally `"shn-md5"`/`"orig-shn-md5"` vs `"flac-md5"`. `126338`'s matching body apparently isn't described with the exact string `"flac-md5"`, so it fell through to the generic `else` branch, which used the flat `local_md5` (which for a flac-md5-only folder happens to equal `local_md5_flac` anyway, since there's no shn-format data locally) — comparison succeeded correctly, but with the safe `"md5"` label instead of `"md5-flac"`. etreedb's description naming turned out not to be a reliable signal to key safety labeling off of.

**Fix**: stopped trusting etreedb's description string for labeling entirely. `_check_one()` now tries `local_md5_shn` first and `local_md5_flac` absolute last as two independent pairs, *for every body regardless of its declared description* — the label is derived from which LOCAL set actually produced the match, not from what etreedb calls the body. This is safe because plain md5 is content-specific: `local_md5_flac` (flac-referenced hash values) can only ever numerically match a candidate body that's genuinely flac-format data; there's no risk of it coincidentally matching real shn-format data just because it's tried against every body. `_compare_bodies()` simplified accordingly — removed the `_SHN_MD5_DESCS`/`_FLAC_MD5_DESCS` desc-based branching entirely, just passes `local_md5_shn`/`local_md5_flac` uniformly to every body.

**Verified**: reproduced the exact failure mode (etreedb body labeled `"shn-md5"` but containing flac-referenced hash values) and confirmed it now correctly labels `"md5-flac"`. Full regression suite re-run (real-shn-wins, both-bodies-present, flac-only with correct desc, flac-only with misleading desc, flac-only with generic/unknown desc, disc-split union) — all 6 cases pass.

**Status: CONFIRMED APPLIED** — delivered as a fresh full-file `resolution.py` (only file touched this time; `lookup_etree.py`/`output.py` unchanged).

### Bug found, NOT yet fixed

**Bug F2 — flac-md5 presence blocks fallback discovery of better files (`lookup.py` / `parsers.py`)**
`scan_concerts()` only calls `find_fallback_checksum_files()` when `find_checksum_files()` returns completely empty. A folder with a weak flac-md5 file (which `find_checksum_files()` does return, since `.md5` is a preferred-tier extension) never gets its `.flac.st5`/`.shn.st5`/`.st5.txt` fallback looked at, even though that fallback would be far more reliable (audio-only, like `.ffp`). Real example: `gd78-05-13.17406.aud-sonyECM33p.rolfe-weiner.sbeok.t-flac16` — has both a flac-md5 file (currently used, unreliable) and a `.flac.st5` + `.shn.st5.txt` (currently invisible, would be reliable). Confirmed via `inventory_checksum_types.py`: 9 folders in the Torrent collection affected (bucket B above).

**Not yet designed or implemented.** Conceptual fix: fallback discovery should trigger whenever the *only* preferred-tier file present is flac-md5, not only when zero preferred-tier files exist at all.

### Diagnostic scripts (added this session, in repo root)

Read-only, no file modifications. `check_flac_md5_folder_status.py` makes live etreedb API calls; the other two are local-only.

- **`inventory_checksum_types.py`** — classifies every folder in a collection into buckets A–E (see table above) by which checksum-file categories actually exist on disk, independent of the current preferred/fallback gating logic. This is what surfaced Bug F2.
- **`check_flac_md5_only.py`** — reports folders that would have zero usable checksum source if flac-md5 were excluded from matching entirely (no ffp, no st5, no shn-md5).
- **`check_flac_md5_folder_status.py`** — runs the real lookup against a specific list of folders, reports found/ambiguous/not-found and match type. Used to see what current (pre-fix) flac-md5 matches actually look like.

`diagnostics/` subfolder reorg considered, intentionally deferred (repo has no subfolder structure yet, not worth the churn for 3 scripts) — see Deferred Features.

---

## Collaboration Notes

- **sed is unreliable for Python source edits** — use `str_replace` tool or heredoc rewrite. Mac native sed differs from GNU sed; even GNU sed struggles with complex regex escaping in Python source.
- **Verbose test runs** reveal exact code path taken
- **curl output** for direct API debugging
- **Bogus-date pattern `??/??/39`** is reliable compilation signal — all JOTW and GD Compilations aggregator entries use it
- **Multi-file patch generation needs verification before delivery** — this session, a `git diff -- fileA fileB` patch was delivered to the user missing fileB's hunk entirely (silently — the command didn't error, it just diffed against a stale baseline). Caught only because the user tested against real data and noticed a case that should've been fixed wasn't. Lesson: always `cat`/inspect a generated multi-file patch's `diff --git` headers before calling it done, especially after any stash/commit-based isolation trickery.

---

## Known Issues / Open Items

### Under Investigation
- 2218 upgrade case: comments contain standard `href="/shn/99233">upgrade` — should work with current regex. Can't verify — folder no longer exists. Watch for recurrence.

### Deferred Features
- **Recursive folder scanning**: Replace `--depth 1|2` with recursive walk. Any folder with checksum file = concert. "Shallowest folder wins" if nesting. Warn on nested checksum folders. Same logic for `--build-index`. Implement as test script first.
- **`diagnostics/` folder reorg**: repo currently has no subfolder structure, so the three standalone diagnostic scripts (`inventory_checksum_types.py`, `check_flac_md5_only.py`, `check_flac_md5_folder_status.py`) live in the repo root alongside the production modules. Moving them into a `diagnostics/` folder would be cleaner but is intentionally deferred — repo is small enough that it's not worth the churn yet.

---

## File Locations

- **Repo**: local path varies — all production code
- **GDCloud**: `~/Music/GDCloud/` — organized by decade/year subdirectory  
  - Full path: `~/Library/Mobile Documents/com~apple~CloudDocs/gd/`
- **Torrent**: `~/Music/Torrent/` — concerts 2 levels deep (CollectionFolder/concertFolder)
- **Torrent index**: `torrent_index.json` alongside the code (in repo root)
- **Test folder**: `~/Music/GDCloud/test/` — 19 symlinks covering all code branches

---

## GraphQL Query Reference

```bash
# Check comments for a SHNID (e.g. for upgrade links)
curl -s -X POST https://graphql.etreedb.org/ \
  -H "Content-Type: application/json" \
  -d '{"query":"{ sources(filter:{id:{eq:SHNID}}) { edges { node { comments } } } }"}' \
  | python3 -m json.tool

# Check hash body structure for multiple SHNIDs
curl -s -X POST https://graphql.etreedb.org/ \
  -H "Content-Type: application/json" \
  -d '{"query":"{ sources(filter:{id:{in:[A,B]}}) { edges { node { id checksums { edges { node { description body } } } } } } }"}' \
  | python3 -m json.tool

# Check if a hash exists in etreedb
curl -s -X POST https://graphql.etreedb.org/ \
  -H "Content-Type: application/json" \
  -d '{"query":"{ checksums(filter: { body: { contains: \"HASH\" } }) { edges { node { source { id } } } } }"}' \
  | python3 -m json.tool

# Count hashes in each candidate body (debug subset detection)
python3 -c "
import sys; sys.path.insert(0, '.')
from api import graphql, SOURCES_QUERY
from resolution import candidate_hash_sets
data = graphql(SOURCES_QUERY, {'ids': [A, B]})
bulk_nodes = {}
for edge in data['data']['sources']['edges']:
    node = edge['node']; shnid = str(node['id']); bulk_nodes[shnid] = node
    bodies = node.get('checksums', {}).get('edges', [])
    print(f'SHNID {shnid}: {len(bodies)} bodies')
    for b in bodies:
        desc = b['node'].get('description',''); body = b['node'].get('body','')
        print(f'  {desc}: {len(body.splitlines())} lines')
hash_sets = candidate_hash_sets(bulk_nodes)
for s, hs in hash_sets.items(): print(f'SHNID {s}: {len(hs)} hashes')
ha = hash_sets.get(str(A), set()); hb = hash_sets.get(str(B), set())
print(f'A < B: {ha < hb}, A == B: {ha == hb}, overlap: {len(ha & hb)}')
"
```
