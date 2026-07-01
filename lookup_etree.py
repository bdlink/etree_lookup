"""
lookup_etree.py — Orchestrate SHNID lookup against etreedb.org.

This module is the public interface for the lookup operation.
It coordinates:
  1. Probing etreedb with a local hash to get initial candidates  (api)
  2. Fetching all candidate checksum bodies for local resolution  (api)
  3. Resolving which candidate matches the local hashes           (resolution)
  4. Following upgrade chains from source comments               (upgrades)

All GraphQL calls go through api.py; all hash comparison logic lives
in resolution.py; upgrade traversal lives in upgrades.py.
"""

import time
from typing import Optional

from api import (
    graphql,
    CHECKSUM_QUERY,
    SOURCES_QUERY,
    source_to_meta,
)
from resolution import resolve, MatchDetail, candidate_hash_sets
from upgrades import extract_upgrade_shnid, fetch_upgrade_chain, fetch_upgrade_chains


# ---------------------------------------------------------------------------
# Local hash classification helpers
# ---------------------------------------------------------------------------

def _split_by_type(checksums: list[tuple]) -> tuple[set[str], set[str], set[str]]:
    """Return (md5_hashes, ffp_hashes, st5_hashes) as separate sets."""
    md5 = {h for h, _, t in checksums if t == "md5"}
    ffp = {h for h, _, t in checksums if t == "ffp"}
    st5 = {h for h, _, t in checksums if t == "st5"}
    return md5, ffp, st5


def _priority_ordered(checksums: list[tuple]) -> list[tuple]:
    """
    Return checksums ordered md5 → ffp → st5, deduplicated by hash value.
    st5 is last-resort: only used when no md5/ffp hashes are available.
    """
    seen: set[str] = set()
    result = []
    for pref in ("md5", "ffp", "st5"):
        for h, f, t in checksums:
            if t == pref and h not in seen:
                seen.add(h)
                result.append((h, f, t))
    return result


def _build_empty_result() -> dict:
    """Return a canonical result dict with all keys at their default values."""
    return {
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
        "comments":         "",
        "matched_hash":     None,
        "matched_hash_type": None,
        "queries_made":     0,
        "st5_only":         False,
        "precise_used":        False,
        "precise_match":       None,
        "precise_failed":      False,
        "precise_missing":     [],
        "precise_extra":       [],
        "precise_extra_local": [],   # local tracks not in etreedb (filler warning)
        "upgrades":         [],
        "lookup_error":     None,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def lookup_shnid(
    checksums: list[tuple],
    verbose:   bool  = False,
    precise:   bool  = False,
    inter_query_delay: float = 0.2,
) -> Optional[dict]:
    """
    Find the SHNID for a concert given its local checksum entries.

    Strategy (at most 3 API calls per concert):
      1. Probe with one hash (md5 preferred, ffp fallback, st5 last resort)
         to get initial candidate SHNIDs.
      2. If ambiguous or --precise: bulk-fetch all candidate checksum bodies
         and resolve locally — the correct SHNID must exactly match the
         local hashes.
      3. Follow the upgrade chain from the winner's comments field.

    Returns a result dict (see _build_empty_result for shape) or None
    if no hash probe matched anything in etreedb.
    """
    ordered   = _priority_ordered(checksums)
    if not ordered:
        return None

    local_md5, local_ffp, local_st5 = _split_by_type(ordered)
    st5_only = not local_md5 and not local_ffp
    queries_made = 0

    # ------------------------------------------------------------------
    # Step 1: probe with first available hash to get initial candidates
    # ------------------------------------------------------------------
    probes = []
    for pref in ("md5", "ffp", "st5"):
        pool = [(h, f, t) for h, f, t in ordered if t == pref]
        if pool:
            probes.append(pool[0])

    edges: list = []
    probe_hash = probe_filename = probe_type = None
    network_error: Optional[str] = None

    for probe_hash, probe_filename, probe_type in probes:
        if verbose:
            print(f"    Probe — {probe_type}: {probe_hash[:12]}… ({probe_filename})")
        try:
            data = graphql(CHECKSUM_QUERY, {"hash": probe_hash})
        except RuntimeError as exc:
            if verbose:
                print(f"    API error: {exc}")
            network_error = str(exc)
            continue
        network_error = None  # this probe succeeded at the network level
        queries_made += 1
        edges = data.get("data", {}).get("checksums", {}).get("edges", [])
        if edges:
            if verbose:
                print(f"    Got results with {probe_type} probe.")
            break
        if verbose:
            print(f"    No match with {probe_type} — trying next.")

    if not edges:
        if network_error:
            if verbose:
                print(f"    All probes failed due to network error: {network_error}")
            raise RuntimeError(f"network error during probe: {network_error}")
        if verbose:
            print("    No match in etreedb.")
        return None

    # Collect candidate metadata from probe results
    initial_metadata: dict[str, dict] = {}
    for edge in edges:
        node   = edge.get("node", {})
        source = node.get("source") or {}
        shnid  = source.get("id")
        if shnid and str(shnid) not in initial_metadata:
            initial_metadata[str(shnid)] = source_to_meta(source)

    if verbose:
        print(f"    Candidates: {', '.join(sorted(initial_metadata))}")

    # ------------------------------------------------------------------
    # Step 2: resolve (always for multiple candidates; also for single
    # candidate when --precise is set)
    # ------------------------------------------------------------------
    survivors: set[str]
    precise_survivors: dict[str, MatchDetail] = {}
    precise_failures:  dict[str, MatchDetail] = {}

    needs_resolution = len(initial_metadata) > 1 or precise

    if not needs_resolution:
        survivors = set(initial_metadata.keys())
        if verbose:
            print("    Unambiguous after probe.")
    else:
        reason = "precise" if len(initial_metadata) == 1 else f"{len(initial_metadata)} candidates"
        if verbose:
            print(f"    Bulk-fetching bodies ({reason}) for local resolution…")

        candidate_ids = [int(s) for s in initial_metadata if s.isdigit()]
        if inter_query_delay:
            time.sleep(inter_query_delay)

        try:
            bulk_data = graphql(SOURCES_QUERY, {"ids": candidate_ids})
        except RuntimeError as exc:
            if verbose:
                print(f"    Bulk fetch error: {exc}")
            survivors = set(initial_metadata.keys())
        else:
            queries_made += 1
            bulk_nodes: dict[str, dict] = {}
            for edge in bulk_data.get("data", {}).get("sources", {}).get("edges", []):
                node  = edge.get("node", {})
                shnid = node.get("id")
                if shnid:
                    s = str(shnid)
                    bulk_nodes[s] = node
                    if s in initial_metadata:
                        initial_metadata[s].update(source_to_meta(node))

            precise_survivors, precise_failures = resolve(
                bulk_nodes, local_md5, local_ffp,
                local_st5=local_st5 if st5_only else None,
                verbose=verbose,
            )
            survivors = set(precise_survivors.keys())

            # If any candidate matched via a real hash comparison, drop
            # candidates that only passed via probe trust.  A compilation
            # SHNID (e.g. 105772, 147313) may contain one of the probe
            # hashes in its body but have no comparable hash type for a
            # proper comparison — probe trust would otherwise promote it
            # to ambiguous alongside the real match.
            _REAL_MATCH_PREFIXES = ("md5", "ffp", "st5")
            has_real_match = any(
                (precise_survivors[s].match_type or "").startswith(_REAL_MATCH_PREFIXES)
                for s in survivors
            )
            if has_real_match:
                probe_only = {
                    s for s in survivors
                    if precise_survivors[s].match_type == "probe"
                }
                if probe_only and len(survivors) - len(probe_only) >= 1:
                    if verbose:
                        for s in probe_only:
                            print(f"    SHNID {s} eliminated: probe-trust only "
                                  f"(real hash match exists elsewhere)")
                    survivors -= probe_only

            if not survivors:
                # All candidates failed — return best candidate with failure detail
                if verbose:
                    print("    No candidate passed resolution.")
                best = list(initial_metadata.keys())[0]
                failure = precise_failures.get(best)
                result = _build_empty_result()
                result.update(initial_metadata[best])
                result.update({
                    "shnid_list":      [best],
                    "matched_hash":    probe_hash,
                    "matched_hash_type": probe_type,
                    "queries_made":    queries_made,
                    "st5_only":        st5_only,
                    "precise_used":    True,
                    "precise_failed":  True,
                    "precise_missing": failure.missing if failure else [],
                    "precise_extra":   failure.extra   if failure else [],
                })
                return result

    # ------------------------------------------------------------------
    # Step 3: build final result
    # ------------------------------------------------------------------
    shnid_list = sorted(survivors, key=lambda s: int(s) if s.isdigit() else s)
    ambiguous  = len(shnid_list) != 1

    # Bogus-date filter: compilation/aggregator SHNIDs on etreedb often have
    # date ??/??/39 or similar placeholder dates.  If any candidate has a
    # real date and another has a bogus date (contains '??'), eliminate the
    # bogus-date candidate.
    if ambiguous:
        def _is_bogus_date(s):
            return "??" in (initial_metadata.get(s, {}).get("date") or "")
        bogus_dated   = {s for s in shnid_list if _is_bogus_date(s)}
        real_dated    = {s for s in shnid_list if not _is_bogus_date(s)}
        if bogus_dated and real_dated:
            if verbose:
                for s in bogus_dated:
                    d = initial_metadata.get(s, {}).get("date", "?")
                    print(f"    SHNID {s} eliminated: bogus date {d!r} "
                          f"(compilation or aggregator)")
            shnid_list = [s for s in shnid_list if s not in bogus_dated]
            ambiguous  = len(shnid_list) != 1

    # Subset resolution: if one surviving candidate's hash set strictly
    # contains another's, it is a superset (e.g. a compilation) and should
    # be eliminated in favour of the more specific entry whose hashes
    # exactly match the local files.
    subset_note: dict[str, str] = {}  # shnid_str -> explanation
    if ambiguous and needs_resolution:
        hash_sets = candidate_hash_sets(bulk_nodes)
        to_remove: set[str] = set()
        for a in list(shnid_list):
            for b in list(shnid_list):
                if a == b:
                    continue
                ha = hash_sets.get(a, set())
                hb = hash_sets.get(b, set())
                if ha and hb and ha < hb:  # a strictly contained in b → b is superset
                    to_remove.add(b)
                    subset_note[b] = f"superset of {a} (compilation or aggregate)"
                    if verbose:
                        print(f"    SHNID {a} hash set ⊂ SHNID {b} — eliminating {b} (superset)")
        if to_remove and len(shnid_list) - len(to_remove) >= 1:
            shnid_list = [s for s in shnid_list if s not in to_remove]
            ambiguous = len(shnid_list) != 1

    # Identical hash sets: when two candidates have exactly the same hashes,
    # they represent the same audio in different etreedb entries — flag this.
    identical_note: dict[str, str] = {}
    if ambiguous and needs_resolution:
        for i, a in enumerate(shnid_list):
            for b in shnid_list[i+1:]:
                ha = hash_sets.get(a, set())
                hb = hash_sets.get(b, set())
                if ha and hb and ha == hb:
                    identical_note[a] = f"identical audio to {b}"
                    identical_note[b] = f"identical audio to {a}"
                    if verbose:
                        print(f"    SHNID {a} and {b} have identical hash sets")

    if ambiguous:
        if verbose:
            print(f"    Ambiguous: {', '.join(shnid_list)}")
            print("    Fetching upgrade chains for ambiguous candidates…")
        amb_upgrades = fetch_upgrade_chains(
            [int(s) for s in shnid_list if s.isdigit()],
            verbose=verbose, delay=inter_query_delay,
        )
        # Build per-candidate metadata for display, including match detail
        amb_metadata = {}
        for s in shnid_list:
            meta = dict(initial_metadata.get(s, {}))
            detail = precise_survivors.get(s)
            if detail:
                meta["match_description"] = detail.match_description
                meta["match_type"]        = detail.match_type
            amb_metadata[s] = meta
        result = _build_empty_result()
        result.update({
            "shnid_list":         shnid_list,
            "ambiguous":          True,
            "ambiguous_upgrades": amb_upgrades,
            "ambiguous_metadata": amb_metadata,
            "ambiguous_subset_note": subset_note,
            "ambiguous_identical_note": identical_note,
            "matched_hash":       probe_hash,
            "matched_hash_type":  probe_type,
            "queries_made":       queries_made,
            "st5_only":           st5_only,
            "precise_used":       precise,
        })
        return result

    subset_note = {}  # only populated for ambiguous path
    # Single winner
    best   = shnid_list[0]
    result = _build_empty_result()
    result.update(initial_metadata[best])
    result.update({
        "shnid_list":      shnid_list,
        "matched_hash":    probe_hash,
        "matched_hash_type": probe_type,
        "queries_made":    queries_made,
        "st5_only":        st5_only,
    })

    # Precise match detail
    if precise or needs_resolution:
        survivor_detail = precise_survivors.get(best)
        failure_detail  = precise_failures.get(best)
        result["precise_used"] = True
        if survivor_detail and survivor_detail.matched:
            # "probe" means no comparable type — treat as unverifiable, not a match
            match_type = survivor_detail.match_type
            result["precise_match"] = match_type if match_type != "probe" else None
            result["precise_unverifiable"] = (match_type == "probe")
            if "+extra-local" in (match_type or ""):
                result["precise_extra_local"] = survivor_detail.missing
        else:
            result["precise_failed"] = True
            result["precise_missing"] = failure_detail.missing if failure_detail else []
            result["precise_extra"]   = failure_detail.extra   if failure_detail else []

    # Upgrade chain
    upgrade_shnid = extract_upgrade_shnid(result.get("comments", ""))
    if upgrade_shnid:
        if verbose:
            print(f"    Upgrade chain from SHNID {upgrade_shnid}")
        if inter_query_delay:
            time.sleep(inter_query_delay)
        result["upgrades"] = fetch_upgrade_chain(
            upgrade_shnid, verbose=verbose, delay=inter_query_delay)

    return result
