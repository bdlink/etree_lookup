"""
api.py — etreedb.org GraphQL API communication.

All GraphQL queries and the HTTP transport live here.
Nothing else in the codebase makes HTTP calls directly.
"""

import json
import urllib.error
import urllib.request

GRAPHQL_URL = "https://graphql.etreedb.org/"

# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

# Search for sources whose checksum body contains a given hash string.
CHECKSUM_QUERY = """
query FindByChecksum($hash: String!) {
  checksums(filter: { body: { contains: $hash } }) {
    edges {
      node {
        id
        source {
          id
          comments
          performance {
            date
            venue
            city
            state
            artist { name }
          }
        }
      }
    }
  }
}
"""

# Fetch full checksum bodies + metadata for a list of source IDs.
SOURCES_QUERY = """
query FindSources($ids: [Int!]!) {
  sources(filter: { id: { in: $ids } }) {
    edges {
      node {
        id
        comments
        performance {
          date
          venue
          city
          state
          artist { name }
        }
        checksums {
          edges {
            node {
              id
              description
              body
            }
          }
        }
      }
    }
  }
}
"""

# Fetch a single source by ID (used for upgrade chain traversal).
SOURCE_QUERY = """
query FindSource($id: Int!) {
  sources(filter: { id: { eq: $id } }) {
    edges {
      node {
        id
        comments
        performance {
          date
          venue
          city
          state
          artist { name }
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def graphql(query: str, variables: dict) -> dict:
    """
    Execute a GraphQL POST request against etreedb.

    Returns the parsed JSON response dict.
    Raises RuntimeError on HTTP errors or GraphQL-level errors.
    """
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "gd-shnid-lookup/2.0 (python)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from etreedb API: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error reaching etreedb: {e.reason}")

    data = json.loads(raw)
    if "errors" in data:
        msgs = "; ".join(e.get("message", "?") for e in data["errors"])
        raise RuntimeError(f"GraphQL errors: {msgs}")
    return data


# ---------------------------------------------------------------------------
# Convenience extractors (parse raw API responses into plain structures)
# ---------------------------------------------------------------------------

def parse_performance(perf: dict) -> dict:
    """Extract performance fields from a raw performance node."""
    artist_obj = (perf or {}).get("artist") or {}
    return {
        "artist": artist_obj.get("name"),
        "date":   perf.get("date"),
        "venue":  perf.get("venue"),
        "city":   perf.get("city"),
        "state":  perf.get("state"),
    }


def source_to_meta(source: dict) -> dict:
    """Convert a raw source node to a metadata dict."""
    shnid = source.get("id")
    perf  = parse_performance(source.get("performance") or {})
    return {
        "shnid":    shnid,
        "etree_url": f"https://etreedb.org/shn/{shnid}" if shnid else None,
        "comments": source.get("comments") or "",
        **perf,
    }
