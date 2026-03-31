/*
 *  ircd-phatbox: An IRC daemon.
 *  unicode_algo.c: Hand-written Unicode algorithm functions.
 *
 *  NFC normalization, NFD decomposition, canonical composition,
 *  UTS #39 skeleton computation, RFC 5893 bidi checking, and
 *  RFC 8265 PRECIS preparation.
 *
 *  These functions use the generated table lookups from unicode_tables.c
 *  via the public API in unicode_data.h and the internal API in
 *  unicode_tables.h.
 *
 *  Copyright (C) 2026 ircd-phatbox development team
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; either version 2 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301
 *  USA
 */

#include "stdinc.h"
#include "unicode_data.h"
#include "unicode_tables.h"
#include "utf8.h"

/* --- Hangul constants (Unicode §3.12) --- */

#define HANGUL_SBASE  0xAC00
#define HANGUL_LBASE  0x1100
#define HANGUL_VBASE  0x1161
#define HANGUL_TBASE  0x11A7
#define HANGUL_LCOUNT 19
#define HANGUL_VCOUNT 21
#define HANGUL_TCOUNT 28
#define HANGUL_NCOUNT (HANGUL_VCOUNT * HANGUL_TCOUNT)
#define HANGUL_SCOUNT (HANGUL_LCOUNT * HANGUL_NCOUNT)

/* --- Canonical Decomposition --- */

/*
 * Full recursive canonical decomposition into buf.
 * Handles both Hangul (algorithmic) and table-based decompositions.
 * Returns the number of codepoints written, or -1 on overflow.
 */
static int
full_decomp(uint32_t cp, uint32_t *buf, int bufmax)
{
	/* Hangul algorithmic decomposition */
	if(cp >= HANGUL_SBASE && cp < HANGUL_SBASE + HANGUL_SCOUNT)
	{
		int sindex = cp - HANGUL_SBASE;
		int l = HANGUL_LBASE + sindex / HANGUL_NCOUNT;
		int v = HANGUL_VBASE + (sindex % HANGUL_NCOUNT) / HANGUL_TCOUNT;
		int t = HANGUL_TBASE + sindex % HANGUL_TCOUNT;
		int len = (t == HANGUL_TBASE) ? 2 : 3;
		if(len > bufmax)
			return -1;
		buf[0] = l;
		buf[1] = v;
		if(t != HANGUL_TBASE)
			buf[2] = t;
		return len;
	}

	uint32_t tmp[4];
	int dlen = unicode_lookup_decomp(cp, tmp, 4);
	if(dlen <= 0)
	{
		if(bufmax < 1)
			return -1;
		buf[0] = cp;
		return 1;
	}

	/* Recursively decompose each part */
	int total = 0;
	for(int i = 0; i < dlen; i++)
	{
		int n = full_decomp(tmp[i], buf + total, bufmax - total);
		if(n < 0)
			return -1;
		total += n;
	}
	return total;
}

/* --- Canonical Composition --- */

/*
 * Canonical composition with Hangul algorithmic support.
 * Returns the composed codepoint, or 0 if no composition exists.
 */
static uint32_t
compose_pair(uint32_t cp1, uint32_t cp2)
{
	/* Hangul LV composition */
	if(cp1 >= HANGUL_LBASE && cp1 < HANGUL_LBASE + HANGUL_LCOUNT
	   && cp2 >= HANGUL_VBASE && cp2 < HANGUL_VBASE + HANGUL_VCOUNT)
	{
		return HANGUL_SBASE
			+ ((cp1 - HANGUL_LBASE) * HANGUL_VCOUNT
			   + (cp2 - HANGUL_VBASE)) * HANGUL_TCOUNT;
	}
	/* Hangul LVT composition */
	if(cp1 >= HANGUL_SBASE && cp1 < HANGUL_SBASE + HANGUL_SCOUNT
	   && ((cp1 - HANGUL_SBASE) % HANGUL_TCOUNT) == 0
	   && cp2 > HANGUL_TBASE && cp2 < HANGUL_TBASE + HANGUL_TCOUNT)
	{
		return cp1 + (cp2 - HANGUL_TBASE);
	}

	/* Table lookup */
	return unicode_lookup_comp(cp1, cp2);
}

/* --- NFC Normalization --- */

int
unicode_nfc(const uint32_t *input, int inlen, uint32_t *output, int outmax)
{
	/* Step 1: Canonical decomposition */
	uint32_t decomposed[512];
	int dlen = 0;
	for(int i = 0; i < inlen; i++)
	{
		int n = full_decomp(input[i], decomposed + dlen, 512 - dlen);
		if(n < 0)
			return -1;
		dlen += n;
	}

	/* Step 2: Canonical ordering (sort combining marks by CCC) */
	for(int i = 1; i < dlen; i++)
	{
		int ccc = unicode_canonical_class(decomposed[i]);
		if(ccc == 0)
			continue;
		int j = i;
		while(j > 0)
		{
			int prev_ccc = unicode_canonical_class(decomposed[j - 1]);
			if(prev_ccc == 0 || prev_ccc <= ccc)
				break;
			uint32_t tmp = decomposed[j];
			decomposed[j] = decomposed[j - 1];
			decomposed[j - 1] = tmp;
			j--;
		}
	}

	/* Step 3: Canonical composition */
	if(dlen == 0)
		return 0;

	uint32_t composed[512];
	int clen = 0;
	composed[clen++] = decomposed[0];

	for(int i = 1; i < dlen; i++)
	{
		int last_starter = -1;
		/* Find the last starter in composed buffer */
		for(int k = clen - 1; k >= 0; k--)
		{
			if(unicode_canonical_class(composed[k]) == 0)
			{
				last_starter = k;
				break;
			}
		}

		int cur_ccc = unicode_canonical_class(decomposed[i]);

		/* Check if we can compose with the last starter */
		if(last_starter >= 0)
		{
			/*
			 * Blocked check: there must not be a character with
			 * CCC >= cur_ccc between the last starter and current
			 * position (unless cur_ccc == 0).
			 */
			bool blocked = false;
			if(cur_ccc != 0)
			{
				for(int k = last_starter + 1; k < clen; k++)
				{
					int between_ccc = unicode_canonical_class(composed[k]);
					if(between_ccc >= cur_ccc)
					{
						blocked = true;
						break;
					}
				}
			}
			else if(last_starter < clen - 1)
			{
				blocked = true;
			}

			if(!blocked)
			{
				uint32_t comp = compose_pair(composed[last_starter],
							     decomposed[i]);
				if(comp != 0)
				{
					composed[last_starter] = comp;
					continue;
				}
			}
		}

		if(clen >= 512)
			return -1;
		composed[clen++] = decomposed[i];
	}

	if(clen > outmax)
		return -1;
	for(int i = 0; i < clen; i++)
		output[i] = composed[i];
	return clen;
}

/* --- NFD Normalization --- */

/*
 * Canonical decomposition only, no recomposition.
 * Used internally by unicode_skeleton().
 */
static int
unicode_nfd(const uint32_t *input, int inlen, uint32_t *output, int outmax)
{
	int dlen = 0;
	for(int i = 0; i < inlen; i++)
	{
		int n = full_decomp(input[i], output + dlen, outmax - dlen);
		if(n < 0)
			return -1;
		dlen += n;
	}

	/* Canonical ordering */
	for(int i = 1; i < dlen; i++)
	{
		int ccc = unicode_canonical_class(output[i]);
		if(ccc == 0)
			continue;
		int j = i;
		while(j > 0)
		{
			int prev_ccc = unicode_canonical_class(output[j - 1]);
			if(prev_ccc == 0 || prev_ccc <= ccc)
				break;
			uint32_t tmp = output[j];
			output[j] = output[j - 1];
			output[j - 1] = tmp;
			j--;
		}
	}
	return dlen;
}

/* --- Bidi --- */

int
unicode_bidi_class(uint32_t cp)
{
	/*
	 * Simplified bidi class lookup.
	 * TODO: replace with generated table from UnicodeData.txt field[4]
	 * (Phase 2 of the unicode security hardening plan).
	 */
	if(cp < 0x0080)
		return BIDI_L;
	if(cp >= 0x0590 && cp <= 0x05FF)
		return BIDI_R;
	if(cp >= 0x0600 && cp <= 0x06FF)
		return BIDI_AL;
	return BIDI_L;
}

bool
unicode_check_bidi(const uint32_t *cps, int len)
{
	if(len == 0)
		return true;

	int first_class = unicode_bidi_class(cps[0]);

	/*
	 * If label starts with L, it's an LTR label — all chars must be
	 * L, EN, ES, CS, ET, ON, BN, NSM per RFC 5893 Rule 1.
	 */
	if(first_class == BIDI_L)
	{
		for(int i = 0; i < len; i++)
		{
			int bc = unicode_bidi_class(cps[i]);
			if(bc != BIDI_L && bc != BIDI_EN && bc != BIDI_ES &&
			   bc != BIDI_CS && bc != BIDI_ET && bc != BIDI_ON &&
			   bc != BIDI_BN && bc != BIDI_NSM)
				return false;
		}
		return true;
	}

	/*
	 * If label starts with R or AL, it's an RTL label.
	 * RFC 5893 Rules 2-6.
	 */
	if(first_class == BIDI_R || first_class == BIDI_AL)
	{
		/* Rule 2: only R, AL, AN, EN, ES, CS, ET, ON, BN, NSM */
		for(int i = 0; i < len; i++)
		{
			int bc = unicode_bidi_class(cps[i]);
			if(bc != BIDI_R && bc != BIDI_AL && bc != BIDI_AN &&
			   bc != BIDI_EN && bc != BIDI_ES && bc != BIDI_CS &&
			   bc != BIDI_ET && bc != BIDI_ON && bc != BIDI_BN &&
			   bc != BIDI_NSM)
				return false;
		}

		/* Rule 3: last non-NSM char must be R or AL or AN or EN */
		int last_bc = -1;
		for(int i = len - 1; i >= 0; i--)
		{
			last_bc = unicode_bidi_class(cps[i]);
			if(last_bc != BIDI_NSM)
				break;
		}
		if(last_bc != BIDI_R && last_bc != BIDI_AL &&
		   last_bc != BIDI_AN && last_bc != BIDI_EN)
			return false;

		return true;
	}

	/* Label doesn't start with L, R, or AL — reject */
	return false;
}

/* --- UTS #39 Skeleton --- */

int
unicode_skeleton(const uint32_t *input, int inlen, uint32_t *output, int outmax)
{
	uint32_t buf1[512], buf2[512];

	/* Step 1: NFD */
	int len = unicode_nfd(input, inlen, buf1, 512);
	if(len < 0)
		return -1;

	/* Step 2: Strip default-ignorable codepoints */
	int stripped = 0;
	for(int i = 0; i < len; i++)
	{
		if(!unicode_is_default_ignorable(buf1[i]))
			buf2[stripped++] = buf1[i];
	}

	/* Step 3: Apply confusable mappings */
	int mapped = 0;
	for(int i = 0; i < stripped; i++)
	{
		uint32_t tmp[4];
		int n = unicode_confusable_map(buf2[i], tmp, 4);
		if(n > 0)
		{
			if(mapped + n > 512)
				return -1;
			for(int j = 0; j < n; j++)
				buf1[mapped++] = tmp[j];
		}
		else
		{
			if(mapped >= 512)
				return -1;
			buf1[mapped++] = buf2[i];
		}
	}

	/* Step 4: NFD again */
	return unicode_nfd(buf1, mapped, output, outmax);
}

/* --- PRECIS (RFC 8265 UsernameCaseMapped) --- */

int
precis_prepare_nick(const char *input, uint32_t *output, int outmax)
{
	const unsigned char *p = (const unsigned char *)input;
	uint32_t cps[128];
	int cplen = 0;

	/* Decode UTF-8 */
	while(*p && cplen < 128)
	{
		uint32_t cp;
		if(utf8_decode(&p, &cp) < 0)
			return -1;
		cps[cplen++] = cp;
	}
	if(*p) /* too long */
		return -1;

	/* Width mapping */
	for(int i = 0; i < cplen; i++)
		cps[i] = unicode_width_map(cps[i]);

	/* NFC normalization */
	uint32_t nfc_buf[128];
	int nfc_len = unicode_nfc(cps, cplen, nfc_buf, 128);
	if(nfc_len < 0)
		return -1;

	/* Category check: Letter, Mark, Digit only (ASCII handled by caller) */
	for(int i = 0; i < nfc_len; i++)
	{
		uint32_t cp = nfc_buf[i];
		if(cp < 0x80)
			continue; /* ASCII chars validated by caller */
		if(!unicode_is_letter(cp) && !unicode_is_mark(cp)
		   && !unicode_is_digit(cp))
			return -1;
	}

	/* Case fold (full: one codepoint may expand to up to 3) */
	uint32_t fold_buf[384]; /* 128 * CASEFOLD_MAX_EXPANSION */
	int fold_len = 0;
	for(int i = 0; i < nfc_len; i++)
	{
		int n = unicode_casefold_full(nfc_buf[i],
					      fold_buf + fold_len,
					      384 - fold_len);
		if(n < 0)
			return -1;
		fold_len += n;
	}

	/* Bidi check */
	if(!unicode_check_bidi(fold_buf, fold_len))
		return -1;

	if(fold_len > outmax)
		return -1;
	for(int i = 0; i < fold_len; i++)
		output[i] = fold_buf[i];
	return fold_len;
}
