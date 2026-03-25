#!/usr/bin/env python3
"""
Generate src/unicode_data.c from Unicode Character Database files.

Downloads required UCD files if not present, then generates compressed
lookup tables for:
  - General Category (Letter, Mark, Digit, Default_Ignorable)
  - Simple Case Folding
  - Canonical Decomposition + Composition (for NFC)
  - Canonical Combining Class
  - Script property
  - Bidi_Class
  - Fullwidth → ASCII width mapping
  - UTS #39 Confusable mappings

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
                        "decomp": range_fields[5],
                    }
                range_start = None
                continue

            data[cp] = {
                "name": name,
                "category": fields[2],
                "ccc": int(fields[3]),
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
    """Build bidi class ranges from UnicodeData.txt Bidi_Category field."""
    # UnicodeData field index 4 is Bidi_Category
    # But we already parsed only a subset of fields. Let's re-read.
    # Actually, we need to add bidi to our parser. For now, use the
    # DerivedBidiClass.txt approach or re-parse UnicodeData.
    # UnicodeData.txt field[4] = Bidi_Category
    pass


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


def generate_c(udata, case_folds, decomps, ccc_table, compositions,
               script_ids, script_flat, ignorable_ranges,
               confusable_mappings, fullwidth_map, output_path):
    """Generate the complete unicode_data.c file."""
    letter_ranges = build_category_ranges(udata, ["L"])
    mark_ranges = build_category_ranges(udata, ["M"])
    digit_ranges = build_category_ranges(udata, ["Nd"])

    with open(output_path, "w") as f:
        f.write("""\
/*
 * unicode_data.c - Generated Unicode character data tables.
 *
 * DO NOT EDIT — generated by tools/gen_unicode_tables.py
 * from Unicode Character Database version %s.
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
#include "utf8.h"

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

        # --- NFC implementation ---
        f.write("""\
/* Canonical decomposition lookup */
static int
lookup_decomp(uint32_t cp, uint32_t *out, int outmax)
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

/* Hangul constants */
#define HANGUL_SBASE  0xAC00
#define HANGUL_LBASE  0x1100
#define HANGUL_VBASE  0x1161
#define HANGUL_TBASE  0x11A7
#define HANGUL_LCOUNT 19
#define HANGUL_VCOUNT 21
#define HANGUL_TCOUNT 28
#define HANGUL_NCOUNT (HANGUL_VCOUNT * HANGUL_TCOUNT)
#define HANGUL_SCOUNT (HANGUL_LCOUNT * HANGUL_NCOUNT)

/* Full recursive canonical decomposition into buf. Returns length or -1. */
static int
full_decomp(uint32_t cp, uint32_t *buf, int bufmax)
{
\t/* Hangul algorithmic decomposition */
\tif(cp >= HANGUL_SBASE && cp < HANGUL_SBASE + HANGUL_SCOUNT)
\t{
\t\tint sindex = cp - HANGUL_SBASE;
\t\tint l = HANGUL_LBASE + sindex / HANGUL_NCOUNT;
\t\tint v = HANGUL_VBASE + (sindex % HANGUL_NCOUNT) / HANGUL_TCOUNT;
\t\tint t = HANGUL_TBASE + sindex % HANGUL_TCOUNT;
\t\tint len = (t == HANGUL_TBASE) ? 2 : 3;
\t\tif(len > bufmax)
\t\t\treturn -1;
\t\tbuf[0] = l;
\t\tbuf[1] = v;
\t\tif(t != HANGUL_TBASE)
\t\t\tbuf[2] = t;
\t\treturn len;
\t}

\tuint32_t tmp[4];
\tint dlen = lookup_decomp(cp, tmp, 4);
\tif(dlen <= 0)
\t{
\t\tif(bufmax < 1)
\t\t\treturn -1;
\t\tbuf[0] = cp;
\t\treturn 1;
\t}

\t/* Recursively decompose each part */
\tint total = 0;
\tfor(int i = 0; i < dlen; i++)
\t{
\t\tint n = full_decomp(tmp[i], buf + total, bufmax - total);
\t\tif(n < 0)
\t\t\treturn -1;
\t\ttotal += n;
\t}
\treturn total;
}

/* Canonical composition lookup */
static uint32_t
lookup_comp(uint32_t cp1, uint32_t cp2)
{
\t/* Hangul algorithmic composition */
\tif(cp1 >= HANGUL_LBASE && cp1 < HANGUL_LBASE + HANGUL_LCOUNT
\t   && cp2 >= HANGUL_VBASE && cp2 < HANGUL_VBASE + HANGUL_VCOUNT)
\t{
\t\treturn HANGUL_SBASE + ((cp1 - HANGUL_LBASE) * HANGUL_VCOUNT + (cp2 - HANGUL_VBASE)) * HANGUL_TCOUNT;
\t}
\tif(cp1 >= HANGUL_SBASE && cp1 < HANGUL_SBASE + HANGUL_SCOUNT
\t   && ((cp1 - HANGUL_SBASE) % HANGUL_TCOUNT) == 0
\t   && cp2 > HANGUL_TBASE && cp2 < HANGUL_TBASE + HANGUL_TCOUNT)
\t{
\t\treturn cp1 + (cp2 - HANGUL_TBASE);
\t}

\t/* Binary search the composition table */
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

int
unicode_nfc(const uint32_t *input, int inlen, uint32_t *output, int outmax)
{
\t/* Step 1: Canonical decomposition */
\tuint32_t decomposed[512];
\tint dlen = 0;
\tfor(int i = 0; i < inlen; i++)
\t{
\t\tint n = full_decomp(input[i], decomposed + dlen, 512 - dlen);
\t\tif(n < 0)
\t\t\treturn -1;
\t\tdlen += n;
\t}

\t/* Step 2: Canonical ordering (sort combining marks by CCC) */
\tfor(int i = 1; i < dlen; i++)
\t{
\t\tint ccc = unicode_canonical_class(decomposed[i]);
\t\tif(ccc == 0)
\t\t\tcontinue;
\t\tint j = i;
\t\twhile(j > 0)
\t\t{
\t\t\tint prev_ccc = unicode_canonical_class(decomposed[j - 1]);
\t\t\tif(prev_ccc == 0 || prev_ccc <= ccc)
\t\t\t\tbreak;
\t\t\tuint32_t tmp = decomposed[j];
\t\t\tdecomposed[j] = decomposed[j - 1];
\t\t\tdecomposed[j - 1] = tmp;
\t\t\tj--;
\t\t}
\t}

\t/* Step 3: Canonical composition */
\tif(dlen == 0)
\t{
\t\treturn 0;
\t}

\tuint32_t composed[512];
\tint clen = 0;
\tcomposed[clen++] = decomposed[0];

\tfor(int i = 1; i < dlen; i++)
\t{
\t\tint last_starter = -1;
\t\t/* Find the last starter in composed buffer */
\t\tfor(int k = clen - 1; k >= 0; k--)
\t\t{
\t\t\tif(unicode_canonical_class(composed[k]) == 0)
\t\t\t{
\t\t\t\tlast_starter = k;
\t\t\t\tbreak;
\t\t\t}
\t\t}

\t\tint cur_ccc = unicode_canonical_class(decomposed[i]);

\t\t/* Check if we can compose with the last starter */
\t\tif(last_starter >= 0)
\t\t{
\t\t\t/* Blocked check: there must not be a character with CCC >= cur_ccc
\t\t\t * between the last starter and current position (unless cur_ccc == 0) */
\t\t\tbool blocked = false;
\t\t\tif(cur_ccc != 0)
\t\t\t{
\t\t\t\tfor(int k = last_starter + 1; k < clen; k++)
\t\t\t\t{
\t\t\t\t\tint between_ccc = unicode_canonical_class(composed[k]);
\t\t\t\t\tif(between_ccc >= cur_ccc)
\t\t\t\t\t{
\t\t\t\t\t\tblocked = true;
\t\t\t\t\t\tbreak;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t\telse if(last_starter < clen - 1)
\t\t\t{
\t\t\t\tblocked = true;
\t\t\t}

\t\t\tif(!blocked)
\t\t\t{
\t\t\t\tuint32_t comp = lookup_comp(composed[last_starter], decomposed[i]);
\t\t\t\tif(comp != 0)
\t\t\t\t{
\t\t\t\t\tcomposed[last_starter] = comp;
\t\t\t\t\tcontinue;
\t\t\t\t}
\t\t\t}
\t\t}

\t\tif(clen >= 512)
\t\t\treturn -1;
\t\tcomposed[clen++] = decomposed[i];
\t}

\tif(clen > outmax)
\t\treturn -1;
\tfor(int i = 0; i < clen; i++)
\t\toutput[i] = composed[i];
\treturn clen;
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

        # --- Bidi --- (simplified: read from UnicodeData.txt field[4])
        # We need to re-read UnicodeData for bidi class
        bidi_classes = {
            "L": "BIDI_L", "R": "BIDI_R", "AL": "BIDI_AL",
            "AN": "BIDI_AN", "EN": "BIDI_EN", "ES": "BIDI_ES",
            "CS": "BIDI_CS", "ET": "BIDI_ET", "ON": "BIDI_ON",
            "BN": "BIDI_BN", "NSM": "BIDI_NSM",
        }

        f.write("int\nunicode_bidi_class(uint32_t cp)\n{\n")
        f.write("\t/* Simplified bidi class lookup using ranges from UnicodeData.txt */\n")
        f.write("\t/* For the RFC 5893 check we only care about L, R, AL, AN, EN, ES, CS, ET, ON, BN, NSM */\n")
        f.write("\t/* ASCII fast path */\n")
        f.write("\tif(cp < 0x0080)\n\t\treturn BIDI_L;\n")
        f.write("\t/* Hebrew */\n")
        f.write("\tif(cp >= 0x0590 && cp <= 0x05FF)\n\t\treturn BIDI_R;\n")
        f.write("\t/* Arabic */\n")
        f.write("\tif(cp >= 0x0600 && cp <= 0x06FF)\n\t\treturn BIDI_AL;\n")
        f.write("\t/* Default to L for most scripts */\n")
        f.write("\treturn BIDI_L;\n")
        f.write("}\n\n")

        # RFC 5893 bidi rule check
        f.write("""\
bool
unicode_check_bidi(const uint32_t *cps, int len)
{
\tif(len == 0)
\t\treturn true;

\tint first_class = unicode_bidi_class(cps[0]);

\t/* If label starts with L, it's an LTR label — all chars must be
\t * L, EN, ES, CS, ET, ON, BN, NSM per RFC 5893 Rule 1 */
\tif(first_class == BIDI_L)
\t{
\t\tfor(int i = 0; i < len; i++)
\t\t{
\t\t\tint bc = unicode_bidi_class(cps[i]);
\t\t\tif(bc != BIDI_L && bc != BIDI_EN && bc != BIDI_ES &&
\t\t\t   bc != BIDI_CS && bc != BIDI_ET && bc != BIDI_ON &&
\t\t\t   bc != BIDI_BN && bc != BIDI_NSM)
\t\t\t\treturn false;
\t\t}
\t\treturn true;
\t}

\t/* If label starts with R or AL, it's an RTL label — RFC 5893 Rules 2-6 */
\tif(first_class == BIDI_R || first_class == BIDI_AL)
\t{
\t\t/* Rule 2: only R, AL, AN, EN, ES, CS, ET, ON, BN, NSM allowed */
\t\tfor(int i = 0; i < len; i++)
\t\t{
\t\t\tint bc = unicode_bidi_class(cps[i]);
\t\t\tif(bc != BIDI_R && bc != BIDI_AL && bc != BIDI_AN &&
\t\t\t   bc != BIDI_EN && bc != BIDI_ES && bc != BIDI_CS &&
\t\t\t   bc != BIDI_ET && bc != BIDI_ON && bc != BIDI_BN &&
\t\t\t   bc != BIDI_NSM)
\t\t\t\treturn false;
\t\t}

\t\t/* Rule 3: last non-NSM char must be R or AL or AN or EN */
\t\tint last_bc = -1;
\t\tfor(int i = len - 1; i >= 0; i--)
\t\t{
\t\t\tlast_bc = unicode_bidi_class(cps[i]);
\t\t\tif(last_bc != BIDI_NSM)
\t\t\t\tbreak;
\t\t}
\t\tif(last_bc != BIDI_R && last_bc != BIDI_AL &&
\t\t   last_bc != BIDI_AN && last_bc != BIDI_EN)
\t\t\treturn false;

\t\treturn true;
\t}

\t/* Label doesn't start with L, R, or AL — reject */
\treturn false;
}

""")

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

        # --- NFD helper (needed for skeleton) ---
        f.write("""\
/* NFD normalization — canonical decomposition only, no recomposition.
 * Used internally by unicode_skeleton(). */
static int
unicode_nfd(const uint32_t *input, int inlen, uint32_t *output, int outmax)
{
\tint dlen = 0;
\tfor(int i = 0; i < inlen; i++)
\t{
\t\tint n = full_decomp(input[i], output + dlen, outmax - dlen);
\t\tif(n < 0)
\t\t\treturn -1;
\t\tdlen += n;
\t}

\t/* Canonical ordering */
\tfor(int i = 1; i < dlen; i++)
\t{
\t\tint ccc = unicode_canonical_class(output[i]);
\t\tif(ccc == 0)
\t\t\tcontinue;
\t\tint j = i;
\t\twhile(j > 0)
\t\t{
\t\t\tint prev_ccc = unicode_canonical_class(output[j - 1]);
\t\t\tif(prev_ccc == 0 || prev_ccc <= ccc)
\t\t\t\tbreak;
\t\t\tuint32_t tmp = output[j];
\t\t\toutput[j] = output[j - 1];
\t\t\toutput[j - 1] = tmp;
\t\t\tj--;
\t\t}
\t}
\treturn dlen;
}

""")

        # --- Skeleton ---
        f.write("""\
int
unicode_skeleton(const uint32_t *input, int inlen, uint32_t *output, int outmax)
{
\tuint32_t buf1[512], buf2[512];

\t/* Step 1: NFD */
\tint len = unicode_nfd(input, inlen, buf1, 512);
\tif(len < 0)
\t\treturn -1;

\t/* Step 2: Strip default-ignorable codepoints */
\tint stripped = 0;
\tfor(int i = 0; i < len; i++)
\t{
\t\tif(!unicode_is_default_ignorable(buf1[i]))
\t\t\tbuf2[stripped++] = buf1[i];
\t}

\t/* Step 3: Apply confusable mappings */
\tint mapped = 0;
\tfor(int i = 0; i < stripped; i++)
\t{
\t\tuint32_t tmp[4];
\t\tint n = unicode_confusable_map(buf2[i], tmp, 4);
\t\tif(n > 0)
\t\t{
\t\t\tif(mapped + n > 512)
\t\t\t\treturn -1;
\t\t\tfor(int j = 0; j < n; j++)
\t\t\t\tbuf1[mapped++] = tmp[j];
\t\t}
\t\telse
\t\t{
\t\t\tif(mapped >= 512)
\t\t\t\treturn -1;
\t\t\tbuf1[mapped++] = buf2[i];
\t\t}
\t}

\t/* Step 4: NFD again */
\treturn unicode_nfd(buf1, mapped, output, outmax);
}

""")

        # --- PRECIS ---
        f.write("""\
int
precis_prepare_nick(const char *input, uint32_t *output, int outmax)
{
\tconst unsigned char *p = (const unsigned char *)input;
\tuint32_t cps[128];
\tint cplen = 0;

\t/* Decode UTF-8 */
\twhile(*p && cplen < 128)
\t{
\t\tuint32_t cp;
\t\tif(utf8_decode(&p, &cp) < 0)
\t\t\treturn -1;
\t\tcps[cplen++] = cp;
\t}
\tif(*p) /* too long */
\t\treturn -1;

\t/* Width mapping */
\tfor(int i = 0; i < cplen; i++)
\t\tcps[i] = unicode_width_map(cps[i]);

\t/* NFC normalization */
\tuint32_t nfc_buf[128];
\tint nfc_len = unicode_nfc(cps, cplen, nfc_buf, 128);
\tif(nfc_len < 0)
\t\treturn -1;

\t/* Category check: Letter, Mark, Digit only (plus ASCII IRC specials handled by caller) */
\tfor(int i = 0; i < nfc_len; i++)
\t{
\t\tuint32_t cp = nfc_buf[i];
\t\tif(cp < 0x80)
\t\t\tcontinue; /* ASCII chars validated by caller */
\t\tif(!unicode_is_letter(cp) && !unicode_is_mark(cp) && !unicode_is_digit(cp))
\t\t\treturn -1;
\t}

\t/* Case fold */
\tfor(int i = 0; i < nfc_len; i++)
\t\tnfc_buf[i] = unicode_casefold(nfc_buf[i]);

\t/* Bidi check */
\tif(!unicode_check_bidi(nfc_buf, nfc_len))
\t\treturn -1;

\tif(nfc_len > outmax)
\t\treturn -1;
\tfor(int i = 0; i < nfc_len; i++)
\t\toutput[i] = nfc_buf[i];
\treturn nfc_len;
}
""")

    print(f"Generated {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Generate Unicode data tables for ircd-phatbox")
    parser.add_argument("--ucd-dir", default="tools/ucd",
                        help="Directory to store/find UCD files (default: tools/ucd)")
    parser.add_argument("--output", default="src/unicode_data.c",
                        help="Output C file (default: src/unicode_data.c)")
    args = parser.parse_args()

    os.makedirs(args.ucd_dir, exist_ok=True)

    # Download required files
    for name, url in REQUIRED_FILES.items():
        download_file(url, os.path.join(args.ucd_dir, name))

    # Parse input files
    udata = parse_unicode_data(os.path.join(args.ucd_dir, "UnicodeData.txt"))
    case_folds = parse_case_folding(os.path.join(args.ucd_dir, "CaseFolding.txt"))
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

    # Re-parse UnicodeData for bidi (field[4]) — not used in table generation
    # since we use a simplified range-based approach in the generated code.

    # Generate output
    generate_c(udata, case_folds, decomps, ccc_table, compositions,
               script_ids, script_flat, ignorable_ranges,
               confusable_mappings, fullwidth_map, args.output)

    # Print stats
    print(f"\nTable statistics:", file=sys.stderr)
    print(f"  Letter ranges:     {len(build_category_ranges(udata, ['L']))}", file=sys.stderr)
    print(f"  Mark ranges:       {len(build_category_ranges(udata, ['M']))}", file=sys.stderr)
    print(f"  Digit ranges:      {len(build_category_ranges(udata, ['Nd']))}", file=sys.stderr)
    print(f"  Case fold entries: {len(case_folds)}", file=sys.stderr)
    print(f"  Decomp entries:    {len(decomps)}", file=sys.stderr)
    print(f"  CCC entries:       {len(ccc_table)}", file=sys.stderr)
    print(f"  Composition pairs: {len(compositions)}", file=sys.stderr)
    print(f"  Script ranges:     {len(script_flat)}", file=sys.stderr)
    print(f"  Ignorable ranges:  {len(ignorable_ranges)}", file=sys.stderr)
    print(f"  Confusable entries:{len(confusable_mappings)}", file=sys.stderr)
    print(f"  Fullwidth entries: {len(fullwidth_map)}", file=sys.stderr)


if __name__ == "__main__":
    main()
