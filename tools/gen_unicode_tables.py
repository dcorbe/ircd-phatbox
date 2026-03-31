#!/usr/bin/env python3
"""
Generate src/unicode_tables.c from Unicode Character Database files.

Downloads required UCD files if not present, then generates compressed
lookup tables and thin binary-search lookup functions for:
  - General Category (Letter, Mark, Digit, Default_Ignorable)
  - Simple Case Folding
  - Canonical Decomposition + Composition table lookups
  - Canonical Combining Class
  - Script property
  - Fullwidth → ASCII width mapping
  - UTS #39 Confusable mappings

Algorithm functions (NFC, NFD, skeleton, bidi, PRECIS) are hand-written
in src/unicode_algo.c and use the generated table lookups.

Usage:
    python3 tools/gen_unicode_tables.py [--ucd-dir DIR] [--output FILE]

The generated file is intended to be checked into version control.
"""

import argparse
import os
import sys
import urllib.request
from collections import defaultdict

UNICODE_VERSION = "16.0.0"
UCD_BASE = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd"
SECURITY_BASE = f"https://www.unicode.org/Public/security/{UNICODE_VERSION}"

REQUIRED_FILES = {
    "UnicodeData.txt": f"{UCD_BASE}/UnicodeData.txt",
    "CaseFolding.txt": f"{UCD_BASE}/CaseFolding.txt",
    "Scripts.txt": f"{UCD_BASE}/Scripts.txt",
    "DerivedCoreProperties.txt": f"{UCD_BASE}/DerivedCoreProperties.txt",
    "CompositionExclusions.txt": f"{UCD_BASE}/CompositionExclusions.txt",
    "confusables.txt": f"{SECURITY_BASE}/confusables.txt",
}


def download_file(url, path):
    """Download a file if it doesn't exist."""
    if os.path.exists(path):
        return
    print(f"Downloading {url} ...", file=sys.stderr)
    urllib.request.urlretrieve(url, path)


def parse_unicode_data(path):
    """Parse UnicodeData.txt, return dict of cp -> fields.

    Handles range markers like '<CJK Ideograph, First>' / '<..., Last>'
    by expanding the range and assigning the same category to all codepoints.
    """
    data = {}
    range_start = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            fields = line.split(";")
            cp = int(fields[0], 16)
            name = fields[1]

            if name.endswith(", First>"):
                range_start = cp
                range_fields = fields
                continue

            if name.endswith(", Last>") and range_start is not None:
                # Expand the range
                for rcp in range(range_start, cp + 1):
                    data[rcp] = {
                        "name": range_fields[1],
                        "category": range_fields[2],
                        "ccc": int(range_fields[3]),
                        "bidi": range_fields[4],
                        "decomp": range_fields[5],
                    }
                range_start = None
                continue

            data[cp] = {
                "name": name,
                "category": fields[2],
                "ccc": int(fields[3]),
                "bidi": fields[4],
                "decomp": fields[5],
            }
    return data


def build_category_ranges(udata, categories):
    """Build sorted list of (start, end) ranges for given category prefixes."""
    cps = sorted(cp for cp, d in udata.items()
                 if any(d["category"].startswith(cat) for cat in categories))
    if not cps:
        return []
    ranges = []
    start = end = cps[0]
    for cp in cps[1:]:
        if cp == end + 1:
            end = cp
        else:
            ranges.append((start, end))
            start = end = cp
    ranges.append((start, end))
    return ranges


def parse_case_folding(path):
    """Parse CaseFolding.txt, return list of (from, to) for status S and C."""
    folds = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            status = parts[1].strip()
            if status in ("S", "C"):
                cp_from = int(parts[0].strip(), 16)
                cp_to = int(parts[2].strip().split()[0], 16)
                folds.append((cp_from, cp_to))
    return sorted(folds)


def parse_full_case_folding(path):
    """Parse CaseFolding.txt, return list of (from, [to_cps]) for status F."""
    folds = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            status = parts[1].strip()
            if status == "F":
                cp_from = int(parts[0].strip(), 16)
                cp_to = [int(x, 16) for x in parts[2].strip().split()]
                folds.append((cp_from, cp_to))
    return sorted(folds)


def build_canonical_decomp(udata):
    """Extract canonical decompositions (not compatibility) from UnicodeData."""
    decomps = {}
    for cp, d in udata.items():
        raw = d["decomp"]
        if not raw:
            continue
        # Skip compatibility decompositions (those starting with <tag>)
        if raw.startswith("<"):
            continue
        parts = [int(x, 16) for x in raw.split()]
        decomps[cp] = parts
    return decomps


def build_ccc_table(udata):
    """Build list of (cp, ccc) for non-zero CCC values."""
    return sorted((cp, d["ccc"]) for cp, d in udata.items() if d["ccc"] != 0)


def parse_composition_exclusions(path):
    """Parse CompositionExclusions.txt."""
    excluded = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cp_str = line.split("#")[0].strip()
            if cp_str:
                excluded.add(int(cp_str, 16))
    return excluded


def build_composition_table(decomps, exclusions, udata):
    """Build canonical composition pairs: (cp1, cp2) -> composed.

    Only for pairs that are not excluded and where the decomposition
    is canonical (2 codepoints, first has CCC=0).
    """
    compositions = {}
    for composed, parts in decomps.items():
        if len(parts) != 2:
            continue
        if composed in exclusions:
            continue
        # Singleton decompositions are excluded
        # The first character must be a starter (CCC=0)
        if udata.get(parts[0], {}).get("ccc", 0) != 0:
            continue
        # Check that this is a Primary Composite (not a Hangul syllable — handled algorithmically)
        if 0xAC00 <= composed <= 0xD7A3:
            continue
        compositions[(parts[0], parts[1])] = composed
    return compositions


def parse_scripts(path):
    """Parse Scripts.txt, return dict of cp -> script_name."""
    scripts = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            cp_range = parts[0].strip()
            script = parts[1].split("#")[0].strip()
            if ".." in cp_range:
                start, end = cp_range.split("..")
                for cp in range(int(start, 16), int(end, 16) + 1):
                    scripts[cp] = script
            else:
                scripts[int(cp_range, 16)] = script
    return scripts


def build_script_ranges(scripts_dict):
    """Build per-script range lists, return (script_names, ranges_by_script)."""
    # Collect all script names, assign IDs (Common=0, Inherited=1, rest alphabetical)
    all_scripts = sorted(set(scripts_dict.values()))
    script_ids = {}
    script_ids["Common"] = 0
    script_ids["Inherited"] = 1
    idx = 2
    for s in all_scripts:
        if s not in script_ids:
            script_ids[s] = idx
            idx += 1

    # Build ranges per script
    by_script = defaultdict(list)
    for cp, script in sorted(scripts_dict.items()):
        by_script[script].append(cp)

    # Compress into ranges
    ranges_by_id = {}
    for script, cps in by_script.items():
        sid = script_ids[script]
        cps = sorted(cps)
        ranges = []
        start = end = cps[0]
        for cp in cps[1:]:
            if cp == end + 1:
                end = cp
            else:
                ranges.append((start, end))
                start = end = cp
        ranges.append((start, end))
        ranges_by_id[sid] = ranges

    # Build a single flat table: (start, end, script_id)
    flat = []
    for sid, ranges in ranges_by_id.items():
        for s, e in ranges:
            flat.append((s, e, sid))
    flat.sort()

    return script_ids, flat


def parse_derived_core_properties(path):
    """Parse DerivedCoreProperties.txt for Default_Ignorable_Code_Point."""
    ignorables = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            prop = parts[1].split("#")[0].strip()
            if prop != "Default_Ignorable_Code_Point":
                continue
            cp_range = parts[0].strip()
            if ".." in cp_range:
                start, end = cp_range.split("..")
                ignorables.append((int(start, 16), int(end, 16)))
            else:
                cp = int(cp_range, 16)
                ignorables.append((cp, cp))
    return sorted(ignorables)


def parse_confusables(path):
    """Parse confusables.txt, return list of (from_cp, [to_cps])."""
    mappings = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")
            if len(parts) < 3:
                continue
            cp_from = int(parts[0].strip(), 16)
            to_str = parts[1].strip()
            cp_to = [int(x, 16) for x in to_str.split()]
            mappings.append((cp_from, cp_to))
    return sorted(mappings)


def build_bidi_ranges(udata):
    """Build bidi class ranges from UnicodeData.txt Bidi_Category field.

    Returns a flat sorted list of (start, end, bidi_class_id) tuples.
    Only includes the 11 classes needed for RFC 5893.
    """
    bidi_class_ids = {
        "L": 0, "R": 1, "AL": 2, "AN": 3, "EN": 4,
        "ES": 5, "CS": 6, "ET": 7, "ON": 8, "BN": 9, "NSM": 10,
    }

    # Collect codepoints per bidi class
    by_class = defaultdict(list)
    for cp, d in udata.items():
        bidi = d.get("bidi")
        if bidi in bidi_class_ids:
            by_class[bidi].append(cp)

    # Compress into ranges and build flat table
    flat = []
    for bidi_name, cps in by_class.items():
        class_id = bidi_class_ids[bidi_name]
        cps = sorted(cps)
        if not cps:
            continue
        start = end = cps[0]
        for cp in cps[1:]:
            if cp == end + 1:
                end = cp
            else:
                flat.append((start, end, class_id))
                start = end = cp
        flat.append((start, end, class_id))

    flat.sort()
    return flat


def build_fullwidth_map():
    """Fullwidth ASCII variants: U+FF01..U+FF5E map to U+0021..U+007E."""
    mappings = []
    for i in range(0xFF01, 0xFF5F):
        mappings.append((i, i - 0xFF01 + 0x0021))
    return mappings


def write_ranges(f, name, ranges, doc=""):
    """Write a range table as a C array."""
    if doc:
        f.write(f"/* {doc} */\n")
    f.write(f"static const struct unicode_range {name}[] = {{\n")
    for i, (start, end) in enumerate(ranges):
        f.write(f"\t{{0x{start:04X}, 0x{end:04X}}},\n")
    f.write("};\n\n")


def write_pair_table(f, name, pairs, doc=""):
    """Write a (from, to) mapping table as a C array."""
    if doc:
        f.write(f"/* {doc} */\n")
    f.write(f"static const struct unicode_pair {name}[] = {{\n")
    for cp_from, cp_to in pairs:
        f.write(f"\t{{0x{cp_from:04X}, 0x{cp_to:04X}}},\n")
    f.write("};\n\n")


def generate_c(udata, case_folds, full_case_folds, decomps, ccc_table,
               compositions, script_ids, script_flat, bidi_flat,
               ignorable_ranges, confusable_mappings, fullwidth_map,
               output_path):
    """Generate unicode_tables.c — data tables and thin lookup functions.

    Algorithm functions (NFC, NFD, skeleton, bidi, PRECIS) live in the
    hand-written unicode_algo.c and are NOT generated here.
    """
    letter_ranges = build_category_ranges(udata, ["L"])
    mark_ranges = build_category_ranges(udata, ["M"])
    digit_ranges = build_category_ranges(udata, ["Nd"])

    with open(output_path, "w") as f:
        f.write("""\
/*
 * unicode_tables.c - Generated Unicode character data tables.
 *
 * DO NOT EDIT — generated by tools/gen_unicode_tables.py
 * from Unicode Character Database version %s.
 *
 * This file contains data tables and thin binary-search lookup
 * functions.  Algorithm functions (NFC, skeleton, PRECIS, bidi)
 * live in the hand-written unicode_algo.c.
 *
 * Tables included:
 *   - General Category ranges (Letter, Mark, Digit)
 *   - Default_Ignorable_Code_Point ranges
 *   - Simple Case Folding
 *   - Canonical Decomposition
 *   - Canonical Combining Class
 *   - Canonical Composition
 *   - Script property
 *   - Fullwidth → ASCII mapping
 *   - UTS #39 Confusable mappings
 */

#include "stdinc.h"
#include "unicode_data.h"
#include "unicode_tables.h"

/* --- Lookup helpers --- */

struct unicode_range {
\tuint32_t start;
\tuint32_t end;
};

struct unicode_pair {
\tuint32_t from;
\tuint32_t to;
};

struct unicode_script_range {
\tuint32_t start;
\tuint32_t end;
\tint script_id;
};

struct unicode_ccc_entry {
\tuint32_t cp;
\tint ccc;
};

struct unicode_decomp_entry {
\tuint32_t cp;
\tuint32_t decomp[4]; /* max canonical decomp length is 2, pad for safety */
\tint len;
};

struct unicode_comp_entry {
\tuint32_t cp1;
\tuint32_t cp2;
\tuint32_t composed;
};

struct unicode_full_casefold_entry {
\tuint32_t from;
\tuint32_t to[3]; /* max full fold is 3 codepoints */
\tint len;
};

struct unicode_bidi_range {
\tuint32_t start;
\tuint32_t end;
\tint bidi_class;
};

struct unicode_confusable_entry {
\tuint32_t from;
\tuint32_t to[4]; /* most confusable mappings are 1-3 codepoints */
\tint len;
};

static bool
in_ranges(uint32_t cp, const struct unicode_range *ranges, int count)
{
\tint lo = 0, hi = count - 1;
\twhile(lo <= hi)
\t{
\t\tint mid = (lo + hi) / 2;
\t\tif(cp < ranges[mid].start)
\t\t\thi = mid - 1;
\t\telse if(cp > ranges[mid].end)
\t\t\tlo = mid + 1;
\t\telse
\t\t\treturn true;
\t}
\treturn false;
}

static uint32_t
lookup_pair(uint32_t cp, const struct unicode_pair *table, int count)
{
\tint lo = 0, hi = count - 1;
\twhile(lo <= hi)
\t{
\t\tint mid = (lo + hi) / 2;
\t\tif(cp < table[mid].from)
\t\t\thi = mid - 1;
\t\telse if(cp > table[mid].from)
\t\t\tlo = mid + 1;
\t\telse
\t\t\treturn table[mid].to;
\t}
\treturn cp;
}

""" % UNICODE_VERSION)

        # --- Category tables ---
        write_ranges(f, "letter_ranges", letter_ranges,
                     f"General_Category L* — {len(letter_ranges)} ranges")
        write_ranges(f, "mark_ranges", mark_ranges,
                     f"General_Category M* — {len(mark_ranges)} ranges")
        write_ranges(f, "digit_ranges", digit_ranges,
                     f"General_Category Nd — {len(digit_ranges)} ranges")
        write_ranges(f, "ignorable_ranges", ignorable_ranges,
                     f"Default_Ignorable_Code_Point — {len(ignorable_ranges)} ranges")

        # --- Category functions ---
        f.write("bool\nunicode_is_letter(uint32_t cp)\n{\n")
        f.write(f"\treturn in_ranges(cp, letter_ranges, {len(letter_ranges)});\n}}\n\n")
        f.write("bool\nunicode_is_mark(uint32_t cp)\n{\n")
        f.write(f"\treturn in_ranges(cp, mark_ranges, {len(mark_ranges)});\n}}\n\n")
        f.write("bool\nunicode_is_digit(uint32_t cp)\n{\n")
        f.write(f"\treturn in_ranges(cp, digit_ranges, {len(digit_ranges)});\n}}\n\n")
        f.write("bool\nunicode_is_default_ignorable(uint32_t cp)\n{\n")
        f.write(f"\treturn in_ranges(cp, ignorable_ranges, {len(ignorable_ranges)});\n}}\n\n")

        # --- Case folding ---
        write_pair_table(f, "casefold_table", case_folds,
                         f"Simple Case Folding — {len(case_folds)} entries")
        f.write("uint32_t\nunicode_casefold(uint32_t cp)\n{\n")
        f.write(f"\treturn lookup_pair(cp, casefold_table, {len(case_folds)});\n}}\n\n")

        # --- Full Case Folding (status F entries: one-to-many) ---
        f.write(f"/* Full Case Folding (status F) — {len(full_case_folds)} entries */\n")
        f.write("static const struct unicode_full_casefold_entry full_casefold_table[] = {\n")
        for cp_from, cp_to_list in full_case_folds:
            padded = cp_to_list[:3] + [0] * (3 - len(cp_to_list[:3]))
            hexparts = ", ".join(f"0x{p:04X}" for p in padded)
            f.write(f"\t{{0x{cp_from:04X}, {{{hexparts}}}, {len(cp_to_list)}}},\n")
        f.write("};\n\n")

        f.write(f"#define FULL_CASEFOLD_TABLE_SIZE {len(full_case_folds)}\n\n")

        f.write("int\nunicode_casefold_full(uint32_t cp, uint32_t *out, int outmax)\n{\n")
        f.write("\t/* Check full folding table first (status F: one-to-many) */\n")
        f.write("\tint lo = 0, hi = FULL_CASEFOLD_TABLE_SIZE - 1;\n")
        f.write("\twhile(lo <= hi)\n\t{\n")
        f.write("\t\tint mid = (lo + hi) / 2;\n")
        f.write("\t\tif(cp < full_casefold_table[mid].from)\n\t\t\thi = mid - 1;\n")
        f.write("\t\telse if(cp > full_casefold_table[mid].from)\n\t\t\tlo = mid + 1;\n")
        f.write("\t\telse\n\t\t{\n")
        f.write("\t\t\tint len = full_casefold_table[mid].len;\n")
        f.write("\t\t\tif(len > outmax)\n\t\t\t\treturn -1;\n")
        f.write("\t\t\tfor(int i = 0; i < len; i++)\n")
        f.write("\t\t\t\tout[i] = full_casefold_table[mid].to[i];\n")
        f.write("\t\t\treturn len;\n")
        f.write("\t\t}\n")
        f.write("\t}\n")
        f.write("\t/* Fall back to simple case fold */\n")
        f.write("\tif(outmax < 1)\n\t\treturn -1;\n")
        f.write("\tout[0] = unicode_casefold(cp);\n")
        f.write("\treturn 1;\n")
        f.write("}\n\n")

        # --- CCC ---
        f.write(f"/* Canonical Combining Class — {len(ccc_table)} non-zero entries */\n")
        f.write("static const struct unicode_ccc_entry ccc_table[] = {\n")
        for cp, ccc in ccc_table:
            f.write(f"\t{{0x{cp:04X}, {ccc}}},\n")
        f.write("};\n\n")

        f.write("int\nunicode_canonical_class(uint32_t cp)\n{\n")
        f.write(f"\tint lo = 0, hi = {len(ccc_table) - 1};\n")
        f.write("\twhile(lo <= hi)\n\t{\n")
        f.write("\t\tint mid = (lo + hi) / 2;\n")
        f.write("\t\tif(cp < ccc_table[mid].cp)\n\t\t\thi = mid - 1;\n")
        f.write("\t\telse if(cp > ccc_table[mid].cp)\n\t\t\tlo = mid + 1;\n")
        f.write("\t\telse\n\t\t\treturn ccc_table[mid].ccc;\n")
        f.write("\t}\n\treturn 0;\n}\n\n")

        # --- Canonical Decomposition ---
        decomp_sorted = sorted(decomps.items())
        f.write(f"/* Canonical Decomposition — {len(decomp_sorted)} entries */\n")
        f.write("static const struct unicode_decomp_entry decomp_table[] = {\n")
        for cp, parts in decomp_sorted:
            padded = parts + [0] * (4 - len(parts))
            hexparts = ", ".join(f"0x{p:04X}" for p in padded)
            f.write(f"\t{{0x{cp:04X}, {{{hexparts}}}, {len(parts)}}},\n")
        f.write("};\n\n")

        f.write(f"#define DECOMP_TABLE_SIZE {len(decomp_sorted)}\n\n")

        # --- Canonical Composition ---
        comp_sorted = sorted(compositions.items())
        f.write(f"/* Canonical Composition — {len(comp_sorted)} pairs */\n")
        f.write("static const struct unicode_comp_entry comp_table[] = {\n")
        for (cp1, cp2), composed in comp_sorted:
            f.write(f"\t{{0x{cp1:04X}, 0x{cp2:04X}, 0x{composed:04X}}},\n")
        f.write("};\n\n")

        f.write(f"#define COMP_TABLE_SIZE {len(comp_sorted)}\n\n")

        # --- Decomposition and Composition table lookups ---
        # These are table-only (no Hangul). Hangul algorithmic handling
        # lives in the hand-written unicode_algo.c.
        f.write("""\
/* Canonical decomposition table lookup (no Hangul). */
int
unicode_lookup_decomp(uint32_t cp, uint32_t *out, int outmax)
{
\tint lo = 0, hi = DECOMP_TABLE_SIZE - 1;
\twhile(lo <= hi)
\t{
\t\tint mid = (lo + hi) / 2;
\t\tif(cp < decomp_table[mid].cp)
\t\t\thi = mid - 1;
\t\telse if(cp > decomp_table[mid].cp)
\t\t\tlo = mid + 1;
\t\telse
\t\t{
\t\t\tint len = decomp_table[mid].len;
\t\t\tif(len > outmax)
\t\t\t\treturn -1;
\t\t\tfor(int i = 0; i < len; i++)
\t\t\t\tout[i] = decomp_table[mid].decomp[i];
\t\t\treturn len;
\t\t}
\t}
\treturn 0; /* no decomposition */
}

/* Canonical composition table lookup (no Hangul). */
uint32_t
unicode_lookup_comp(uint32_t cp1, uint32_t cp2)
{
\tint lo = 0, hi = COMP_TABLE_SIZE - 1;
\twhile(lo <= hi)
\t{
\t\tint mid = (lo + hi) / 2;
\t\tif(cp1 < comp_table[mid].cp1 ||
\t\t   (cp1 == comp_table[mid].cp1 && cp2 < comp_table[mid].cp2))
\t\t\thi = mid - 1;
\t\telse if(cp1 > comp_table[mid].cp1 ||
\t\t        (cp1 == comp_table[mid].cp1 && cp2 > comp_table[mid].cp2))
\t\t\tlo = mid + 1;
\t\telse
\t\t\treturn comp_table[mid].composed;
\t}
\treturn 0; /* no composition */
}

""")

        # --- Script property ---
        # Write script enum defines
        f.write("/* Script ID constants */\n")
        for name, sid in sorted(script_ids.items(), key=lambda x: x[1]):
            if sid <= 1:
                continue  # Already defined in header
            cname = name.upper().replace(" ", "_").replace("-", "_")
            f.write(f"#define SCRIPT_{cname} {sid}\n")
        f.write("\n")

        f.write(f"/* Script property — {len(script_flat)} ranges */\n")
        f.write("static const struct unicode_script_range script_table[] = {\n")
        for start, end, sid in script_flat:
            f.write(f"\t{{0x{start:04X}, 0x{end:04X}, {sid}}},\n")
        f.write("};\n\n")

        f.write("int\nunicode_script(uint32_t cp)\n{\n")
        f.write(f"\tint lo = 0, hi = {len(script_flat) - 1};\n")
        f.write("\twhile(lo <= hi)\n\t{\n")
        f.write("\t\tint mid = (lo + hi) / 2;\n")
        f.write("\t\tif(cp < script_table[mid].start)\n\t\t\thi = mid - 1;\n")
        f.write("\t\telse if(cp > script_table[mid].end)\n\t\t\tlo = mid + 1;\n")
        f.write("\t\telse\n\t\t\treturn script_table[mid].script_id;\n")
        f.write("\t}\n\treturn SCRIPT_COMMON;\n}\n\n")

        # --- Bidi class ---
        f.write(f"/* Bidi_Class — {len(bidi_flat)} ranges */\n")
        f.write("static const struct unicode_bidi_range bidi_table[] = {\n")
        for start, end, class_id in bidi_flat:
            f.write(f"\t{{0x{start:04X}, 0x{end:04X}, {class_id}}},\n")
        f.write("};\n\n")

        f.write("int\nunicode_bidi_class(uint32_t cp)\n{\n")
        f.write(f"\tint lo = 0, hi = {len(bidi_flat) - 1};\n")
        f.write("\twhile(lo <= hi)\n\t{\n")
        f.write("\t\tint mid = (lo + hi) / 2;\n")
        f.write("\t\tif(cp < bidi_table[mid].start)\n\t\t\thi = mid - 1;\n")
        f.write("\t\telse if(cp > bidi_table[mid].end)\n\t\t\tlo = mid + 1;\n")
        f.write("\t\telse\n\t\t\treturn bidi_table[mid].bidi_class;\n")
        f.write("\t}\n\treturn BIDI_L; /* default for unassigned */\n}\n\n")

        # --- Width mapping ---
        write_pair_table(f, "fullwidth_table", fullwidth_map,
                         f"Fullwidth → ASCII — {len(fullwidth_map)} entries")
        f.write("uint32_t\nunicode_width_map(uint32_t cp)\n{\n")
        f.write(f"\treturn lookup_pair(cp, fullwidth_table, {len(fullwidth_map)});\n}}\n\n")

        # --- Confusables ---
        f.write(f"/* UTS #39 Confusable mappings — {len(confusable_mappings)} entries */\n")
        f.write("static const struct unicode_confusable_entry confusable_table[] = {\n")
        for cp_from, cp_to_list in confusable_mappings:
            padded = cp_to_list[:4] + [0] * (4 - len(cp_to_list[:4]))
            hexparts = ", ".join(f"0x{p:04X}" for p in padded)
            f.write(f"\t{{0x{cp_from:04X}, {{{hexparts}}}, {min(len(cp_to_list), 4)}}},\n")
        f.write("};\n\n")

        f.write(f"#define CONFUSABLE_TABLE_SIZE {len(confusable_mappings)}\n\n")

        f.write("int\nunicode_confusable_map(uint32_t cp, uint32_t *out, int outmax)\n{\n")
        f.write("\tint lo = 0, hi = CONFUSABLE_TABLE_SIZE - 1;\n")
        f.write("\twhile(lo <= hi)\n\t{\n")
        f.write("\t\tint mid = (lo + hi) / 2;\n")
        f.write("\t\tif(cp < confusable_table[mid].from)\n\t\t\thi = mid - 1;\n")
        f.write("\t\telse if(cp > confusable_table[mid].from)\n\t\t\tlo = mid + 1;\n")
        f.write("\t\telse\n\t\t{\n")
        f.write("\t\t\tint len = confusable_table[mid].len;\n")
        f.write("\t\t\tif(len > outmax)\n\t\t\t\treturn -1;\n")
        f.write("\t\t\tfor(int i = 0; i < len; i++)\n")
        f.write("\t\t\t\tout[i] = confusable_table[mid].to[i];\n")
        f.write("\t\t\treturn len;\n")
        f.write("\t\t}\n")
        f.write("\t}\n\treturn 0;\n}\n\n")

        # NOTE: Algorithm functions (NFC, NFD, skeleton, bidi, PRECIS)
        # are in the hand-written src/unicode_algo.c, not generated here.

    print(f"Generated {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Generate Unicode data tables for ircd-phatbox")
    parser.add_argument("--ucd-dir", default="tools/ucd",
                        help="Directory to store/find UCD files (default: tools/ucd)")
    parser.add_argument("--output", default="src/unicode_tables.c",
                        help="Output C file (default: src/unicode_tables.c)")
    args = parser.parse_args()

    os.makedirs(args.ucd_dir, exist_ok=True)

    # Download required files
    for name, url in REQUIRED_FILES.items():
        download_file(url, os.path.join(args.ucd_dir, name))

    # Parse input files
    udata = parse_unicode_data(os.path.join(args.ucd_dir, "UnicodeData.txt"))
    case_folds = parse_case_folding(os.path.join(args.ucd_dir, "CaseFolding.txt"))
    full_case_folds = parse_full_case_folding(os.path.join(args.ucd_dir, "CaseFolding.txt"))
    decomps = build_canonical_decomp(udata)
    ccc_table = build_ccc_table(udata)
    exclusions = parse_composition_exclusions(os.path.join(args.ucd_dir, "CompositionExclusions.txt"))
    compositions = build_composition_table(decomps, exclusions, udata)
    scripts_dict = parse_scripts(os.path.join(args.ucd_dir, "Scripts.txt"))
    script_ids, script_flat = build_script_ranges(scripts_dict)
    ignorable_ranges = parse_derived_core_properties(
        os.path.join(args.ucd_dir, "DerivedCoreProperties.txt"))
    confusable_mappings = parse_confusables(os.path.join(args.ucd_dir, "confusables.txt"))
    fullwidth_map = build_fullwidth_map()
    bidi_flat = build_bidi_ranges(udata)

    # Generate output
    generate_c(udata, case_folds, full_case_folds, decomps, ccc_table,
               compositions, script_ids, script_flat, bidi_flat,
               ignorable_ranges, confusable_mappings, fullwidth_map,
               args.output)

    # Print stats
    print(f"\nTable statistics:", file=sys.stderr)
    print(f"  Letter ranges:     {len(build_category_ranges(udata, ['L']))}", file=sys.stderr)
    print(f"  Mark ranges:       {len(build_category_ranges(udata, ['M']))}", file=sys.stderr)
    print(f"  Digit ranges:      {len(build_category_ranges(udata, ['Nd']))}", file=sys.stderr)
    print(f"  Case fold entries: {len(case_folds)}", file=sys.stderr)
    print(f"  Full fold entries: {len(full_case_folds)}", file=sys.stderr)
    print(f"  Decomp entries:    {len(decomps)}", file=sys.stderr)
    print(f"  CCC entries:       {len(ccc_table)}", file=sys.stderr)
    print(f"  Composition pairs: {len(compositions)}", file=sys.stderr)
    print(f"  Script ranges:     {len(script_flat)}", file=sys.stderr)
    print(f"  Ignorable ranges:  {len(ignorable_ranges)}", file=sys.stderr)
    print(f"  Confusable entries:{len(confusable_mappings)}", file=sys.stderr)
    print(f"  Bidi ranges:       {len(bidi_flat)}", file=sys.stderr)
    print(f"  Fullwidth entries: {len(fullwidth_map)}", file=sys.stderr)


if __name__ == "__main__":
    main()
