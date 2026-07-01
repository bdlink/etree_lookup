"""
resolution.py — Local hash resolution against etreedb checksum bodies.

Given a set of local hashes and a set of etreedb checksum bodies,
determines which candidate SHNID (if any) exactly matches.

No API calls are made here — this module is purely computational
and fully unit-testable without network access.
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# Hash classification constants
# ---------------------------------------------------------------------------

# etreedb checksum body description → which local hash type it matches
_MD5_DESCRIPTIONS  = {"orig-shn-md5", "shn-md5", "flac-md5"}
_FFP_DESCRIPTIONS  = {"ffp", "flac-ffp"}
_ST5_DESCRIPTIONS  = {"st5"}

# Regex matching a 32-char md5 or 40-char sha1 hash value
_HASH_RE = re.compile(r"[0-9a-f]{32}(?:[0-9a-f]{8})?", re.IGNORECASE)

# Regex for ffp-style lines: filename:hash
_FFP_LINE_RE = re.compile(r"^(.+):([0-9a-fA-F]{32}(?:[0-9a-fA-F]{8})?)$")

# Regex for shntool lines: hash  [shntool]  filename
_SHNTOOL_RE = re.compile(r"^([0-9a-fA-F]{32})\s+\[shntool\]\s+.+$")


# ---------------------------------------------------------------------------
# Hash extraction from etreedb body text
# ---------------------------------------------------------------------------

def extract_hashes(body: str, description: str = "",
                   use_st5: bool = False) -> tuple[set[str], set[str], set[str]]:
    """
    Extract hash values from an etreedb checksum body string.

    Uses the ``description`` field to classify the body type:
      orig-shn-md5, shn-md5, flac-md5  →  md5_hashes
      ffp, flac-ffp                     →  ffp_hashes
      st5                               →  st5_hashes (only when use_st5=True)

    Falls back to line-format detection when description is unrecognised.
    Shntool lines are always skipped in md5 bodies; st5 bodies are skipped
    entirely unless use_st5 is True.

    Returns (md5_hashes, ffp_hashes, st5_hashes).
    """
    desc = (description or "").lower().strip()

    if desc in _ST5_DESCRIPTIONS and not use_st5:
        return set(), set(), set()

    md5_hashes: set[str] = set()
    ffp_hashes: set[str] = set()
    st5_hashes: set[str] = set()

    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue

        ffp_m = _FFP_LINE_RE.match(line)

        if desc in _MD5_DESCRIPTIONS:
            if "shntool" not in line.lower():
                for h in _HASH_RE.findall(line):
                    md5_hashes.add(h.lower())

        elif desc in _FFP_DESCRIPTIONS or ffp_m:
            if ffp_m:
                ffp_hashes.add(ffp_m.group(2).lower())

        elif desc in _ST5_DESCRIPTIONS and use_st5:
            m = _SHNTOOL_RE.match(line)
            if m:
                st5_hashes.add(m.group(1).lower())
            else:
                m2 = re.match(r"^([0-9a-fA-F]{40})\s+\*?.+$", line)
                if m2:
                    st5_hashes.add(m2.group(1).lower())

        else:
            # Unknown description — detect by line format
            if "shntool" in line.lower():
                pass  # skip
            elif ffp_m:
                ffp_hashes.add(ffp_m.group(2).lower())
            else:
                for h in _HASH_RE.findall(line):
                    md5_hashes.add(h.lower())

    return md5_hashes, ffp_hashes, st5_hashes


# ---------------------------------------------------------------------------
# Disc-split detection
# ---------------------------------------------------------------------------

def is_disc_split(descriptions: list[str]) -> bool:
    """Return True if ALL descriptions look like disc labels: d1, d2, d3, …"""
    return bool(descriptions) and all(
        re.match(r"^d\d+$", d, re.IGNORECASE) for d in descriptions if d
    )


def group_bodies_by_description(
        bodies: list[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Group (description, body) pairs by description."""
    groups: dict[str, list] = {}
    for desc, body in bodies:
        groups.setdefault(desc, []).append((desc, body))
    return groups


# ---------------------------------------------------------------------------
# Resolution result types
# ---------------------------------------------------------------------------

class MatchDetail:
    """Outcome of comparing one candidate against local hashes."""
    __slots__ = ("match_type", "match_description", "missing", "extra")

    def __init__(self, match_type: Optional[str],
                 missing: list, extra: list,
                 match_description: str = ""):
        self.match_type        = match_type        # "md5" | "ffp" | "st5" | None
        self.match_description = match_description # etreedb body description e.g. "orig-shn-md5"
        self.missing           = missing           # local hashes absent from candidate
        self.extra             = extra             # candidate hashes absent from local set

    @property
    def matched(self) -> bool:
        return self.match_type is not None


# ---------------------------------------------------------------------------
# Core resolution
# ---------------------------------------------------------------------------

def resolve(
    candidates: dict[str, dict],
    local_md5:  set[str],
    local_ffp:  set[str],
    local_st5:  Optional[set[str]] = None,
    verbose:    bool = False,
) -> tuple[dict[str, MatchDetail], dict[str, MatchDetail]]:
    """
    Compare each candidate's etreedb checksum bodies against the local hashes.

    Returns (survivors, failures) where each is a dict mapping
    shnid_str → MatchDetail.

    Matching rules:
      - md5 and ffp are independent representations; a candidate passes if
        either type matches exactly (local ⊆ candidate AND candidate ⊆ local).
      - st5 is compared similarly, but only when local_st5 is provided.
      - Each body is checked individually first (handles orig-shn-md5 vs
        shn-md5 disambiguation — they must NOT be unioned).
      - Disc-split bodies (d1, d2, …) are unioned as a second pass if no
        individual body matched.
      - If no comparable hash type exists between local and etreedb,
        the candidate is treated as a pass (no basis for rejection).
    """
    survivors: dict[str, MatchDetail] = {}
    failures:  dict[str, MatchDetail] = {}
    use_st5 = bool(local_st5)

    for shnid_str, node in candidates.items():
        bodies = [
            (edge["node"].get("description", ""), edge["node"].get("body", ""))
            for edge in node.get("checksums", {}).get("edges", [])
        ]

        detail = _compare_bodies(bodies, local_md5, local_ffp,
                                 local_st5 or set(), use_st5, verbose, shnid_str)

        if detail.matched:
            survivors[shnid_str] = detail
            if verbose:
                print(f"    SHNID {shnid_str}: exact {detail.match_type} match ✓")
        else:
            failures[shnid_str] = detail
            if verbose:
                m_strs = [h[:12] for h in detail.missing[:3]]
                e_strs = [h[:12] for h in detail.extra[:3]]
                print(f"    SHNID {shnid_str}: ✗  "
                      f"missing {len(detail.missing)} ({m_strs}{'…' if len(detail.missing)>3 else ''})  "
                      f"extra {len(detail.extra)} ({e_strs}{'…' if len(detail.extra)>3 else ''})")

    return survivors, failures


def candidate_hash_sets(candidates: dict[str, dict]) -> dict[str, set[str]]:
    """
    Extract the combined hash set for each candidate from its specific bodies.

    Excludes orig-shn-md5 — that body may be shared between multiple sources
    (e.g. 225 and 5436 share the same audio but 225 has extra filler tracks in
    orig-shn-md5). Using orig-shn-md5 in subset comparison would incorrectly
    make 5436 appear as a subset of 225.
    """
    _SKIP_FOR_SUBSET = {"orig-shn-md5"}
    result: dict[str, set[str]] = {}
    for shnid_str, node in candidates.items():
        all_hashes: set[str] = set()
        for edge in node.get("checksums", {}).get("edges", []):
            desc = edge["node"].get("description", "").lower()
            if desc in _SKIP_FOR_SUBSET:
                continue
            body = edge["node"].get("body", "")
            md5, ffp, st5 = extract_hashes(body, desc, use_st5=True)
            all_hashes |= md5 | ffp | st5
        result[shnid_str] = all_hashes
    return result


def _compare_bodies(
    bodies:    list[tuple[str, str]],
    local_md5: set[str],
    local_ffp: set[str],
    local_st5: set[str],
    use_st5:   bool,
    verbose:   bool,
    shnid_str: str,
) -> MatchDetail:
    """
    Try to find an exact match between local hashes and the given bodies.
    Returns a MatchDetail indicating success or the best failure detail.
    """
    any_cand_md5: set[str] = set()
    any_cand_ffp: set[str] = set()
    any_cand_st5: set[str] = set()

    best_failure = _MatchState()

    # Pass 1: check each body individually.
    # Sort so shn-md5 is tried before orig-shn-md5 — shn-md5 represents the
    # actual content of this specific etreedb entry and is more discriminating.
    # orig-shn-md5 may be shared across multiple sources (e.g. 5649 and 5650
    # share the same orig-shn-md5 body, but have different shn-md5 bodies).
    def _body_sort_key(desc_body):
        desc = desc_body[0].lower()
        order = {"shn-md5": 0, "flac-md5": 1, "ffp": 2, "flac-ffp": 3,
                 "st5": 4, "orig-shn-md5": 5}
        return order.get(desc, 99)
    sorted_bodies = sorted(bodies, key=_body_sort_key)

    matched_desc = ""
    for desc, body in sorted_bodies:
        cand_md5, cand_ffp, cand_st5 = extract_hashes(body, desc, use_st5=True)
        any_cand_md5 |= cand_md5
        any_cand_ffp |= cand_ffp
        any_cand_st5 |= cand_st5
        result = _check_one(cand_md5, cand_ffp, cand_st5,
                            local_md5, local_ffp, local_st5)
        if result.matched:
            matched_desc = desc
            return MatchDetail(result.match_type, [], [], match_description=desc)
        best_failure.update(result)

    # Pass 2: try union of disc-split bodies
    groups = group_bodies_by_description(bodies)
    disc_groups = {
        desc: grp for desc, grp in groups.items()
        if is_disc_split([desc]) and len(grp) == 1
    }
    if len(disc_groups) > 1:
        union_md5: set[str] = set()
        union_ffp: set[str] = set()
        union_st5: set[str] = set()
        for desc, grp in disc_groups.items():
            for d, b in grp:
                cm, cf, cs = extract_hashes(b, d, use_st5=True)
                union_md5 |= cm
                union_ffp |= cf
                union_st5 |= cs
        if verbose:
            print(f"    Trying disc-split union ({', '.join(sorted(disc_groups))})…")
        result = _check_one(union_md5, union_ffp, union_st5,
                            local_md5, local_ffp, local_st5)
        if result.matched:
            disc_descs = "+".join(sorted(disc_groups))
            return MatchDetail(result.match_type, [], [], match_description=disc_descs)
        best_failure.update(result)

    # Trust probe only when no comparable pair exists on both sides
    any_comparison_attempted = (
        (local_ffp and any_cand_ffp) or   # ffp explicit body
        (local_ffp and any_cand_st5) or   # ffp↔st5 shntool fingerprints
        (local_md5 and any_cand_md5) or
        (local_st5 and any_cand_st5)
    )
    if not any_comparison_attempted:
        return MatchDetail("probe", [], [], match_description="probe")

    return MatchDetail(None, best_failure.missing, best_failure.extra)


class _MatchState:
    """Mutable accumulator for the best (fewest errors) failure seen so far."""
    __slots__ = ("match_type", "missing", "extra")

    def __init__(self):
        self.match_type: Optional[str] = None
        self.missing: list = []
        self.extra:   list = []

    @property
    def matched(self) -> bool:
        return self.match_type is not None

    def update(self, other: "_MatchState"):
        """Keep whichever failure has fewer missing+extra hashes.
        Ignores states that had no comparison (both missing and extra empty
        because no counterpart type was found — not because it was a match).
        A genuine match would have returned early via match_type."""
        other_score = len(other.missing) + len(other.extra)
        self_score  = len(self.missing)  + len(self.extra)
        # Only update if other actually attempted a comparison (has any detail)
        # or if self is also empty (both had no comparison, keep either)
        if other_score == 0 and self_score > 0:
            return  # other had no comparison result — don't overwrite real detail
        if (not self.missing and not self.extra) or other_score < self_score:
            self.missing = other.missing
            self.extra   = other.extra


def _check_one(
    cand_md5: set[str], cand_ffp: set[str], cand_st5: set[str],
    local_md5: set[str], local_ffp: set[str], local_st5: set[str],
) -> "_MatchState":
    """
    Check a single set of candidate hashes against local hashes.

    Key insight: shntool computes the same fingerprint for .shn and .flac
    files containing the same audio. So local .ffp hashes (flac fingerprints
    in filename:hash format) are identical in value to etreedb's st5 body
    hashes (shntool fingerprints in hash [shntool] filename format).

    Comparison pairs:
      local_ffp  ↔  candidate_st5   (flac ffp == shntool st5)
      local_md5  ↔  candidate_md5   (shn/flac md5 checksums)
      local_st5  ↔  candidate_st5   (local st5 fallback files)

    Tries all comparable pairs and returns the best result.
    Returns an empty state if no pair exists on both sides (trust probe).
    """
    state = _MatchState()
    extra_local_state: Optional["_MatchState"] = None

    for local_set, cand_set, type_name in [
        (local_ffp, cand_ffp, "ffp"),        # ffp body explicitly labelled ffp
        (local_ffp, cand_st5, "ffp↔st5"),   # ffp fingerprints == shntool st5
        (local_md5, cand_md5, "md5"),
        (local_st5, cand_st5, "st5"),
    ]:
        if not (local_set and cand_set):
            continue
        missing = local_set - cand_set
        extra   = cand_set  - local_set
        if not missing and not extra:
            state.match_type = type_name
            return state
        if not extra and missing:
            # etreedb's hashes are all present locally, but local has extra
            # tracks not known to etreedb — likely filler tracks.
            # Don't return yet — a later pair may give a clean exact match.
            if extra_local_state is None:
                s = _MatchState()
                s.match_type = f"{type_name}+extra-local"
                s.missing = sorted(missing)
                extra_local_state = s
            continue
        state.update_failure(sorted(missing), sorted(extra))

    # If no exact match found but we had an extra-local candidate, use it
    if extra_local_state is not None:
        return extra_local_state

    return state


# Monkey-patch update_failure onto _MatchState
def _update_failure(self, missing: list, extra: list):
    if (not self.missing and not self.extra) or (
        len(missing) + len(extra) < len(self.missing) + len(self.extra)
    ):
        self.missing = missing
        self.extra   = extra

_MatchState.update_failure = _update_failure
