#!/usr/bin/env python3
"""
check_flac_md5_folder_status.py — Run the real etreedb lookup against a
specific list of folders (the ones flagged by check_flac_md5_only.py as
relying solely on flac-referencing md5 hashes) and report what happens
today, before we decide whether to exclude flac-md5 from matching.

This DOES make live API calls to etreedb (read-only GraphQL queries),
same as lookup.py --precise. Rate-limited with the same default delay.

Usage:
    python check_flac_md5_folder_status.py --delay 0.5

    Folder list is embedded below (from the check_flac_md5_only.py run
    against ~/Music/Torrent). Edit FOLDERS if you want to check a
    different set, or pass --file <path> with one folder path per line.
"""

import argparse
import sys
import time
from pathlib import Path

from parsers import find_checksum_files, find_fallback_checksum_files, parse_checksum_file
from lookup_etree import lookup_shnid


FOLDERS = [
    "Grateful Dead Project 1979 Part 7 = 20.3 GB/gd79-10-28.150530.mtx.seamons.ht66.t-flac16",
    "Grateful Dead Project 1979 Part 7 = 20.3 GB/gd79-11-02.124392.mtx.seamons.t-flac16",
    "Grateful Dead Project 1980 Part 10 = 21.8 GB/gd80-11-26.132221.beyerM160.wise.minches.flac1648",
    "Grateful Dead Project 1980 Part 4 = 21.6 GB/gd80-05-29.25993.fob-pset1-set2.nak300.braveman.sbeok.flac16",
    "Grateful Dead Project 1981 Part 1 = 25.5 GB/gd81-02-28.28398.mtx.chappell.sb2c.flac16",
    "Grateful Dead Project 1981 Part 2 = 25.7 GB/gd81-03-10.131701.mtx.seamons.flac16",
    "Grateful Dead Project 1983 Part 10 - 19.4 GB/gd83-10-17.92424.mtx.seamons.fix2.t-flac16",
    "Grateful Dead Project 1983 Part 2 - 18.836 GB/gd83-04-12.017264.sbd-patched.drgseeds.sbeok.t-flac16",
    "Grateful Dead Project 1983 Part 8 - 16.4 GB/gd83-09-11.139652.s2.fob-senn441.silberman.gans-miller-noel.flac16",
    "Grateful Dead Project 1984 Part 1 = 21.5 GB/gd84-04-06.sbd.willy.10159.sbeok.t-flac16",
    "Grateful Dead Project 1984 Part 6 = 18.5 GB/gd84-07-15.125681.mtx.seamons.flac16",
    "Grateful Dead Project 1985 Part 4 = 21.5 GB/gd85-06-14.126338.akg.d'amico.flac16",
    "Grateful Dead Project 1985 Part 9 = 20.5 GB/gd85-10-31.matrix.loy.31189.sbeok.flacf",
    "Grateful Dead Project 1987 Part 3 = 20.6 GB/gd87-04-02.89395.aud-nak300.damico.sbeok.flac16",
    "Grateful Dead Project 1987 Part 4 = 21.4 GB/gd87-04-04.89471.aud-nak300.damico.sbeok.flac16",
    "Grateful Dead Project 1991 Part 3 = 26.1 GB/gd91-04-07.150455.s2.mtx.seamons.t-flac16",
    "Grateful Dead Project 1993 Part 1 = 23.5 GB/gd93-03-11.120865.mtx.seamons.flac16",
    "Grateful Dead Project 1994 Part 5 = 22.5 GB/gd94-10-05.008030.sbd.unknown.t-flac16",
    "Grateful Dead Project 1994 Part 5 = 22.5 GB/gd94-10-14.119611.mtx.seamons.flac16",
    "gd1968_project.p3/gd68-10-08.111148.mtx.gems.flac24",
    "gdead.1965-1966.projects/gd66-03-19.81951.sbd.scotton.sbeok.t-flac",
    "gdead_1969_project.pt7/gd69-11-08.26331.aud.weinberg.warner.sbeok.flac16",
    "gdead_1970_project.2/gd70-02-01.126275.sbd.lee-smith.flac16",
    "gdead_1970_project.5/gd70-06-05.132395.sbd.bear.wise.flac16",
    "gdead_1971_project.1/gd71-02-20.00111.sbd.orf.sbeok.flac16",
    "gdead_1971_project.5/gd71-04-26.126270.sbd.kafer-boswell-smith.flac16",
    "gdead_1974_project_part1/gd74-01-xx.garcia_interview.sbeok.flac2496",
    "gdead_1974_project_part10_fixed/gd74-10-18.110515.BEAR.gems.flac16",
    "gdead_1974_project_part3/gd74-05-25.111301.fob.gems..flac16",
    "gdead_1974_project_part7_fixed/gd74-08-05.xxxxxx.sbd",
    "gdead_1975_project_complete/gd75-09-28.22257.sbd.bertha.sbeok.flac16",
    "gdead_1976_project_1/gd76-06-03.123898.mtx.seamons.ht06.flac16",
    "gdead_1976_project_2_fixed/gd76-06-11.118256.mtx.seamons.flac16",
    "gdead_1976_project_3/gd76-06-19.121140.mtx.seamons.flac16",
    "gdead_1976_project_4/gd76-06-21.121045.mtx.seamons.flac16",
    "gdead_1976_project_4/gd76-06-21.124894.mtx.seamons.ht98.flac16",
    "gdead_1976_project_7/gd76-07-18.14838.FM.bertha-fink.sbeok.flac16",
    "gdead_1978_project.10_fixed/gd78-10-22.00299.sbd.kempa.sbeok.t-flac16",
    "gdead_1978_project.6/gd78-05-13.17406.aud-sonyECM33p.rolfe-weiner.sbeok.t-flac16",
    "gdead_1979_1.fixed/gd79-01-10.132236.fob-nakCM300.gatto.wise.flac16",
    "gdead_1979_1.fixed/gd79-01-15.127556.fob-nak300.rolfe.minches.flac16",
]

TORRENT_ROOT = Path("~/Music/Torrent").expanduser()


def load_checksums(folder: Path):
    """Same file-selection logic as scan_concerts: preferred, else fallback."""
    files = find_checksum_files(folder)
    if not files:
        files = find_fallback_checksum_files(folder)
    checksums = []
    for f in files:
        try:
            checksums.extend(parse_checksum_file(f))
        except Exception:
            pass
    return checksums


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(TORRENT_ROOT),
                        help="Torrent root the listed folders are relative to")
    parser.add_argument("--file", help="Optional file with one folder path per line "
                                       "(overrides the embedded FOLDERS list)")
    parser.add_argument("--delay", type=float, default=0.5)
    args = parser.parse_args()

    root = Path(args.root).expanduser()

    if args.file:
        rel_folders = [line.strip() for line in Path(args.file).read_text().splitlines() if line.strip()]
    else:
        rel_folders = FOLDERS

    found = []
    not_found = []
    ambiguous = []
    errors = []

    for i, rel in enumerate(rel_folders, 1):
        folder = root / rel
        if not folder.is_dir():
            print(f"[{i}/{len(rel_folders)}] MISSING (not a directory): {folder}")
            errors.append(rel)
            continue

        checksums = load_checksums(folder)
        if not checksums:
            print(f"[{i}/{len(rel_folders)}] {folder.name}: no checksums parsed")
            errors.append(rel)
            continue

        try:
            result = lookup_shnid(checksums, verbose=False, precise=True,
                                  inter_query_delay=args.delay)
        except Exception as exc:
            print(f"[{i}/{len(rel_folders)}] {folder.name}: ERROR — {exc}")
            errors.append(rel)
            continue

        if result is None:
            print(f"[{i}/{len(rel_folders)}] {folder.name}: NOT FOUND")
            not_found.append(rel)
        elif result.get("ambiguous"):
            shnids = result.get("shnid_list", [])
            print(f"[{i}/{len(rel_folders)}] {folder.name}: AMBIGUOUS {shnids}")
            ambiguous.append((rel, shnids))
        else:
            shnid = result.get("shnid")
            mtype = result.get("precise_match")
            unverifiable = result.get("precise_unverifiable")
            extra_local = result.get("precise_extra_local") or []
            note = ""
            if unverifiable:
                note = " (probe-trust only, unverifiable)"
            elif extra_local:
                note = f" (+extra-local, {len(extra_local)})"
            print(f"[{i}/{len(rel_folders)}] {folder.name}: FOUND SHNID {shnid} "
                 f"[{mtype}]{note}")
            found.append((rel, shnid, mtype, unverifiable, len(extra_local)))

        time.sleep(args.delay)

    print()
    print("=" * 60)
    print(f"Found:      {len(found)}")
    print(f"Ambiguous:  {len(ambiguous)}")
    print(f"Not found:  {len(not_found)}")
    print(f"Errors:     {len(errors)}")
    print("=" * 60)

    if found:
        probe_trust_only = sum(1 for _, _, _, unverifiable, _ in found if unverifiable)
        extra_local_flagged = sum(1 for _, _, _, _, n in found if n > 0)
        print(f"  Of the found matches: {probe_trust_only} were probe-trust only "
             f"(unverifiable), {extra_local_flagged} showed +extra-local")


if __name__ == "__main__":
    main()
