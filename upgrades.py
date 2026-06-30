"""
upgrades.py — Follow upgrade chains in etreedb source comments.

etreedb sources may contain an HTML link in their comments field pointing
to an upgraded version of the recording:
    <a href="/shninfo_detail.php?shnid=89003">upgrade</a> in circulation

This module parses those links and follows chains recursively.
"""

import re
import time
from typing import Optional

from api import graphql, SOURCE_QUERY, source_to_meta


def extract_upgrade_shnid(comments: str) -> Optional[int]:
    """
    Parse a SHNID from an upgrade link in Source.comments HTML.

    Handles both known etreedb link formats:
        <a href="/shninfo_detail.php?shnid=89003">upgrade</a>
        <a href= "/shn/74220">upgrade</a>
        <a href= "/shninfo_detail.php?shnid=84826">upgrade now in circulation</a>

    Matches any anchor whose text starts with 'upgrade' (case-insensitive).
    Returns the SHNID as int, or None if no upgrade link is present.
    """
    if not comments:
        return None
    m = re.search(
        r'href=\s*"/(?:shninfo_detail\.php\?shnid=|shn/)(\d+)"[^>]*>\s*upgrade',
        comments, re.IGNORECASE)
    return int(m.group(1)) if m else None


def fetch_upgrade_chain(shnid: int, verbose: bool = False,
                        delay: float = 0.2) -> list[dict]:
    """
    Follow the upgrade chain starting from ``shnid``.

    Returns a list of metadata dicts ordered from immediate upgrade to
    the final upgrade in the chain. Stops on cycles or after 10 hops.
    Each dict has: shnid, artist, date, venue, city, state, etree_url, comments.
    """
    chain = []
    seen: set[int] = set()
    current = shnid

    for _ in range(10):
        if current in seen:
            if verbose:
                print(f"    Upgrade chain: cycle at SHNID {current}, stopping.")
            break
        seen.add(current)

        if verbose:
            print(f"    Fetching upgrade SHNID {current}…")
        try:
            data = graphql(SOURCE_QUERY, {"id": current})
        except RuntimeError as exc:
            if verbose:
                print(f"    Error fetching SHNID {current}: {exc}")
            break

        edges = data.get("data", {}).get("sources", {}).get("edges", [])
        if not edges:
            break

        node = edges[0]["node"]
        meta = source_to_meta(node)
        chain.append(meta)

        next_shnid = extract_upgrade_shnid(meta["comments"])
        if not next_shnid:
            break
        if delay:
            time.sleep(delay)
        current = next_shnid

    return chain


def fetch_upgrade_chains(shnids: list[int | str], verbose: bool = False,
                         delay: float = 0.2) -> dict[str, list[dict]]:
    """
    Fetch upgrade chains for multiple SHNIDs.
    Returns {shnid_str: [upgrade_dicts…]} for each SHNID.
    """
    chains: dict[str, list[dict]] = {}
    for shnid in shnids:
        shnid_int = int(shnid)
        try:
            data = graphql(SOURCE_QUERY, {"id": shnid_int})
            edges = data.get("data", {}).get("sources", {}).get("edges", [])
            comments = edges[0]["node"].get("comments", "") if edges else ""
        except RuntimeError:
            comments = ""

        upgrade_shnid = extract_upgrade_shnid(comments)
        if upgrade_shnid:
            if delay:
                time.sleep(delay)
            chains[str(shnid)] = fetch_upgrade_chain(
                upgrade_shnid, verbose=verbose, delay=delay)
        else:
            chains[str(shnid)] = []

    return chains
