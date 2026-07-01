"""
output.py — Format and write lookup results.

Supported formats: text, csv, json

Extension design:
  Add a new format by adding a case to WRITERS and implementing a writer fn.
  Writers receive: results (list of dicts), file object (or sys.stdout).
"""

import csv
import json
import re
import sys
from pathlib import Path
from typing import IO

# Fields included in CSV/JSON output (in order)
FIELDS = [
    "folder_name",
    "shnid",
    "shnid_list",
    "ambiguous",
    "artist",
    "date",
    "venue",
    "city",
    "state",
    "etree_url",
    "matched_hash_type",
    "matched_hash",
    "precise_match",
    "precise_used",
    "precise_failed",
    "st5_only",
    "precise_missing",
    "precise_extra",
    "checksums_found",
    "lookup_error",
    "queries_made",
    "upgrades",
    "folder",
]


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def _write_text(results: list[dict], f: IO, errors_only: bool = False):
    found       = sum(1 for r in results if r.get("shnid") and not r.get("ambiguous") and not r.get("precise_failed"))
    ambiguous   = sum(1 for r in results if r.get("ambiguous"))
    failed      = sum(1 for r in results if r.get("precise_failed"))
    warned      = sum(1 for r in results if r.get("precise_extra_local"))
    not_found   = sum(1 for r in results if not r.get("shnid") and not r.get("ambiguous") and r.get("lookup_error"))
    f.write(f"\n{'='*60}\n")
    warn_str = f", {warned} extra-local" if warned else ""
    f.write(f"Results: {found}/{len(results)} matched, {ambiguous} ambiguous, "
            f"{failed} precise-failed{warn_str}, {not_found} not found\n")
    f.write(f"{'='*60}\n\n")

    def _is_error(r):
        return (r.get("ambiguous") or r.get("precise_failed") or
                r.get("precise_extra_local") or
                not r.get("shnid") or r.get("lookup_error"))
    display = [r for r in results if not errors_only or _is_error(r)]
    for r in display:
        f.write(f"Folder : {r['folder_name']}\n")

        if r.get("ambiguous"):
            amb_meta        = r.get("ambiguous_metadata") or {}
            amb_upgrades    = r.get("ambiguous_upgrades") or {}
            subset_notes    = r.get("ambiguous_subset_note") or {}
            identical_notes = r.get("ambiguous_identical_note") or {}
            folder_name     = r.get("folder_name", "")

            # Extract date (YYMMDD or YYYY-MM-DD) and SHNID from folder name
            folder_date_m = re.search(r'(\d{2,4})[-.]?(\d{2})[-.]?(\d{2})', folder_name)
            folder_date = ""
            if folder_date_m:
                y, mo, d = folder_date_m.groups()
                if len(y) == 2:
                    y = "19" + y
                folder_date = f"{mo}/{d}/{y[2:]}"  # MM/DD/YY to match etreedb format

            folder_shnids = set(re.findall(r'(?<![0-9])([0-9]{3,6})(?![0-9])', folder_name))

            # Find common match description across candidates
            descs = {str(s): (amb_meta.get(str(s)) or {}).get("match_description", "")
                     for s in r.get("shnid_list", [])}
            unique_descs = set(descs.values()) - {"", None}
            if len(unique_descs) == 1 and "orig-shn-md5" in unique_descs:
                shared_note = " — matched via orig-shn-md5 (shared original transfer)"
            elif len(unique_descs) == 1:
                shared_note = f" — matched via {next(iter(unique_descs))}"
            else:
                shared_note = ""

            f.write(f"SHNID  : AMBIGUOUS{shared_note}\n")

            for s in r.get("shnid_list", []):
                meta     = amb_meta.get(str(s)) or {}
                upgrades = amb_upgrades.get(str(s)) or []
                chain    = " → ".join(
                    [str(s)] + [str(u["shnid"]) for u in upgrades])
                note = (subset_notes.get(str(s), "") or
                        identical_notes.get(str(s), ""))

                # Hints from folder name
                hints = []
                if str(s) in folder_shnids:
                    hints.append("folder name match")
                cand_date = (meta.get("date") or "").strip()
                if folder_date and cand_date:
                    # Compare MM/DD/YY — normalise both
                    if folder_date[:5] == cand_date[:5]:
                        hints.append("date matches folder")
                    else:
                        hints.append(f"date {cand_date} ≠ folder {folder_date}")

                match_desc = meta.get("match_description", "")
                match_type = meta.get("match_type", "")
                if match_desc and match_desc not in ("probe",):
                    hints.append(f"matched on {match_desc}")

                all_notes = ", ".join(filter(None, [note] + hints))
                f.write(f"  Candidate {chain}"
                        f"{f' ({all_notes})' if all_notes else ''}:\n")
                f.write(f"    Artist : {meta.get('artist') or '—'}\n")
                f.write(f"    Date   : {meta.get('date') or '—'}\n")
                f.write(f"    Venue  : {meta.get('venue') or '—'}\n")
                f.write(f"    City   : {meta.get('city') or '—'}, "
                        f"{meta.get('state') or '—'}\n")
                f.write(f"    URL    : https://etreedb.org/shn/{s}\n")

        elif r.get("shnid"):
            precise_failed = r.get("precise_failed")
            if precise_failed:
                f.write(f"SHNID  : {r['shnid']} (initial match — precise check failed)\n")
            else:
                f.write(f"SHNID  : {r['shnid']}\n")
            f.write(f"Artist : {r.get('artist') or '—'}\n")
            f.write(f"Date   : {r.get('date') or '—'}\n")
            f.write(f"Venue  : {r.get('venue') or '—'}\n")
            f.write(f"City   : {r.get('city') or '—'}\n")
            f.write(f"State  : {r.get('state') or '—'}\n")
            f.write(f"URL    : {r.get('etree_url') or '—'}\n")
            f.write(f"Match  : {r.get('matched_hash_type', '?')} "
                    f"hash {(r.get('matched_hash') or '')[:16]}… "
                    f"({r.get('queries_made', '?')} queries)\n")
            if r.get("st5_only"):
                f.write(f"Note   : matched via st5 checksums only\n")
            if r.get("precise_used"):
                pm = r.get("precise_match")
                extra_local = r.get("precise_extra_local") or []
                if pm == "probe":
                    f.write(f"Precise: unverifiable — no comparable hash type in etreedb\n")
                elif pm == "ffp↔st5":
                    f.write(f"Precise: exact ffp match ✓ (via etreedb shntool fingerprints)\n")
                elif pm and "+extra-local" in pm:
                    base = pm.replace("+extra-local", "")
                    f.write(f"Precise: exact {base} match ✓\n")
                    if extra_local:
                        f.write(f"Warning: local has {len(extra_local)} track(s) not in etreedb "
                                f"(possible filler):\n")
                        for h in extra_local[:10]:
                            f.write(f"         {h[:12]}\n")
                        if len(extra_local) > 10:
                            f.write(f"         … ({len(extra_local) - 10} more)\n")
                elif pm:
                    f.write(f"Precise: exact {pm} match ✓\n")
                else:
                    missing = r.get("precise_missing") or []
                    extra   = r.get("precise_extra") or []
                    f.write(f"Precise: FAILED — hashes do not match exactly\n")
                    if missing:
                        f.write(f"         Local hashes missing from etreedb ({len(missing)}): "
                                f"{', '.join(h[:12] for h in missing[:5])}"
                                f"{'…' if len(missing) > 5 else ''}\n")
                    if extra:
                        f.write(f"         Etreedb hashes not in local set ({len(extra)}): "
                                f"{', '.join(h[:12] for h in extra[:5])}"
                                f"{'…' if len(extra) > 5 else ''}\n")

            upgrades = r.get("upgrades") or []
            if upgrades:
                f.write(f"Upgrades:\n")
                for u in upgrades:
                    f.write(f"  SHNID {u['shnid']}: {u.get('date', '—')} "
                            f"{u.get('venue', '—')}, {u.get('city', '—')}, "
                            f"{u.get('state', '—')} "
                            f"— {u.get('etree_url', '—')}\n")
            else:
                f.write(f"Upgrades: none\n")

        else:
            reason = r.get("lookup_error") or "unknown"
            f.write(f"SHNID  : NOT FOUND ({reason})\n")

        if r.get("parse_errors"):
            f.write(f"Errors : {'; '.join(r['parse_errors'])}\n")

        f.write("\n")


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def _write_csv(results: list[dict], f: IO, errors_only: bool = False):
    writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    display = [r for r in results if not errors_only or
               r.get("ambiguous") or r.get("precise_failed") or
               not r.get("shnid") or r.get("lookup_error")]
    for r in display:
        writer.writerow(r)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _write_json(results: list[dict], f: IO, errors_only: bool = False):
    display = [r for r in results if not errors_only or
               r.get("ambiguous") or r.get("precise_failed") or
               not r.get("shnid") or r.get("lookup_error")]
    trimmed = [{k: r.get(k) for k in FIELDS} for r in display]
    json.dump(trimmed, f, indent=2, ensure_ascii=False)
    f.write("\n")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

WRITERS = {
    "text": _write_text,
    "csv":  _write_csv,
    "json": _write_json,
}


def print_results(results: list[dict], fmt: str = "text",
                  errors_only: bool = False):
    writer_fn = WRITERS.get(fmt, _write_text)
    writer_fn(results, sys.stdout, errors_only=errors_only)


def write_results(results: list[dict], path: Path, fmt: str = "text",
                  errors_only: bool = False):
    writer_fn = WRITERS.get(fmt, _write_text)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer_fn(results, f, errors_only=errors_only)
