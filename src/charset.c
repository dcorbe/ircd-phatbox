/*
 *  ircd-phatbox: An IRC daemon.
 *  charset.c: Character set abstraction — strict (ASCII/RFC1459) and
 *             permissive (Unicode/UTF-8) mode implementations.
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
#include "ratbox_lib.h"
#include "struct.h"
#include "charset.h"
#include "match.h"
#include "utf8.h"
#include "unicode_data.h"
#include "s_conf.h"
#include "s_log.h"
#include "hash.h"

/*
 * Strict (ASCII/RFC1459) implementations.
 *
 * These wrap the existing CharAttrs table macros and ToUpper/ToLower tables.
 * They provide identical behaviour to the pre-refactor code.
 */

static bool
strict_is_valid_nick_char(const char **p)
{
	if(!IsNickChar(**p))
		return false;
	(*p)++;
	return true;
}

static bool
strict_is_valid_chan_char(const char **p)
{
	if(!IsChanChar(**p))
		return false;
	(*p)++;
	return true;
}

static int
strict_irc_cmp(const char *s1, const char *s2)
{
	return irccmp_rfc1459(s1, s2);
}

static int
strict_hash_fold(const unsigned char **s, uint32_t *out, int outmax)
{
	(void)outmax;
	out[0] = ToUpper(*(*s)++);
	return 1;
}

/* Forward declarations — the actual implementations live in match.c */
extern int match_rfc1459(const char *mask, const char *name);
extern int match_esc_rfc1459(const char *mask, const char *name);

/*
 * Strict charset operations — the default.
 */
static struct charset_ops charset_strict_ops = {
	.is_valid_nick_char = strict_is_valid_nick_char,
	.is_valid_chan_char = strict_is_valid_chan_char,
	.irc_cmp = strict_irc_cmp,
	.hash_fold = strict_hash_fold,
	.wild_match = match_rfc1459,
	.wild_match_esc = match_esc_rfc1459,
	.normalize_nick = NULL,
	.casemapping_name = "rfc1459",
	.charset_name = "ascii",
};

/*
 * Permissive (UTF-8/Unicode) implementations.
 *
 * For bytes < 0x80, these use the same CharAttrs/ToUpper tables as strict mode
 * (preserving RFC1459 bracket case mapping in the ASCII range).  For bytes
 * >= 0x80, they decode UTF-8 and use the Unicode property tables.
 */

/*
 * Helper: case-fold one logical character and advance the pointer.
 * Writes folded codepoint(s) to out (up to outmax).
 * Returns count of codepoints written.
 *
 * For ASCII, uses ToUpper (RFC1459 rules) — always 1 codepoint.
 * For multi-byte UTF-8, decodes and applies full Unicode Case Folding
 * (which may expand one codepoint to up to CASEFOLD_MAX_EXPANSION).
 *
 * On invalid UTF-8, returns the raw byte and advances by 1.
 */
static int
utf8_fold_advance(const unsigned char **s, uint32_t *out, int outmax)
{
	if(**s < 0x80)
	{
		out[0] = ToUpper(*(*s)++);
		return 1;
	}

	uint32_t cp;
	const unsigned char *saved = *s;
	if(utf8_decode(s, &cp) < 0)
	{
		/* Invalid UTF-8 — treat as raw byte */
		out[0] = *saved;
		*s = saved + 1;
		return 1;
	}
	return unicode_casefold_full(cp, out, outmax);
}

static bool
utf8_is_valid_nick_char(const char **p)
{
	unsigned char b = (unsigned char)**p;

	/* ASCII fast path — use existing CharAttrs table */
	if(b < 0x80)
	{
		if(!IsNickChar(b))
			return false;
		(*p)++;
		return true;
	}

	/* Multi-byte UTF-8 */
	const unsigned char *s = (const unsigned char *)*p;
	uint32_t cp;
	if(utf8_decode(&s, &cp) < 0)
		return false;

	if(!unicode_is_letter(cp) && !unicode_is_mark(cp) && !unicode_is_digit(cp))
		return false;

	*p = (const char *)s;
	return true;
}

static bool
utf8_is_valid_chan_char(const char **p)
{
	unsigned char b = (unsigned char)**p;

	/* ASCII fast path */
	if(b < 0x80)
	{
		if(!IsChanChar(b))
			return false;
		(*p)++;
		return true;
	}

	/* Multi-byte UTF-8 — accept any valid sequence */
	const unsigned char *s = (const unsigned char *)*p;
	uint32_t cp;
	if(utf8_decode(&s, &cp) < 0)
		return false;

	*p = (const char *)s;
	return true;
}

static int
utf8_irc_cmp(const char *s1, const char *s2)
{
	const unsigned char *p1 = (const unsigned char *)s1;
	const unsigned char *p2 = (const unsigned char *)s2;
	uint32_t buf1[CASEFOLD_MAX_EXPANSION], buf2[CASEFOLD_MAX_EXPANSION];
	int len1 = 0, pos1 = 0;
	int len2 = 0, pos2 = 0;

	for(;;)
	{
		if(pos1 >= len1)
		{
			if(*p1 == '\0')
				len1 = pos1 = 0;
			else
			{
				len1 = utf8_fold_advance(&p1, buf1, CASEFOLD_MAX_EXPANSION);
				pos1 = 0;
			}
		}
		if(pos2 >= len2)
		{
			if(*p2 == '\0')
				len2 = pos2 = 0;
			else
			{
				len2 = utf8_fold_advance(&p2, buf2, CASEFOLD_MAX_EXPANSION);
				pos2 = 0;
			}
		}

		if(pos1 >= len1 && pos2 >= len2)
			return 0;
		if(pos1 >= len1)
			return -1;
		if(pos2 >= len2)
			return 1;

		uint32_t c1 = buf1[pos1++];
		uint32_t c2 = buf2[pos2++];
		if(c1 != c2)
			return (c1 < c2) ? -1 : 1;
	}
}

static int
utf8_hash_fold(const unsigned char **s, uint32_t *out, int outmax)
{
	return utf8_fold_advance(s, out, outmax);
}

/*
 * UTF-8 aware wildcard matching.
 *
 * Same algorithm as match_rfc1459() but operates on codepoints instead
 * of bytes.  '?' matches one codepoint (1-4 bytes), '*' matches zero
 * or more codepoints.  Case comparison uses utf8_fold_advance().
 */
#define MATCH_MAX_CALLS 512

/*
 * Fold one logical character at s without advancing.
 * Writes folded codepoint(s) to out. Returns count written.
 */
static int
peek_fold(const unsigned char *s, uint32_t *out, int outmax)
{
	if(*s < 0x80)
	{
		out[0] = ToUpper(*s);
		return 1;
	}
	uint32_t cp;
	if(utf8_decode(&s, &cp) < 0)
	{
		out[0] = *s;
		return 1;
	}
	return unicode_casefold_full(cp, out, outmax);
}

/*
 * Compare folded sequences from mask and name at current positions.
 */
static bool
fold_cmp_equal(const unsigned char *m, const unsigned char *n)
{
	uint32_t fm[CASEFOLD_MAX_EXPANSION], fn[CASEFOLD_MAX_EXPANSION];
	int nm = peek_fold(m, fm, CASEFOLD_MAX_EXPANSION);
	int nn = peek_fold(n, fn, CASEFOLD_MAX_EXPANSION);
	if(nm != nn)
		return false;
	for(int i = 0; i < nm; i++)
	{
		if(fm[i] != fn[i])
			return false;
	}
	return true;
}

/* Advance past one codepoint. Returns new pointer. */
static const unsigned char *
advance_cp(const unsigned char *s)
{
	if(*s < 0x80)
		return s + 1;
	uint32_t cp;
	const unsigned char *saved = s;
	if(utf8_decode(&s, &cp) < 0)
		return saved + 1; /* skip invalid byte */
	return s;
}

static int
match_utf8(const char *mask, const char *name)
{
	const unsigned char *m = (const unsigned char *)mask;
	const unsigned char *n = (const unsigned char *)name;
	const unsigned char *ma = m;
	const unsigned char *na = n;
	int wild = 0;
	int calls = 0;

	if(!mask || !name)
		return 0;

	if(*m == '*' && *(m + 1) == '\0')
		return 1;

	while(calls++ < MATCH_MAX_CALLS)
	{
		if(*m == '*')
		{
			while(*m == '*')
				m++;
			wild = 1;
			ma = m;
			na = n;
		}

		if(!*m)
		{
			if(!*n)
				return 1;
			for(m--; (m > (const unsigned char *)mask) && (*m == '?'); m--)
				;
			if(*m == '*' && (m > (const unsigned char *)mask))
				return 1;
			if(!wild)
				return 0;
			m = ma;
			na = advance_cp(na);
			n = na;
		}
		else if(!*n)
		{
			while(*m == '*')
				m++;
			return (*m == 0);
		}
		else if(*m == '?')
		{
			/* '?' matches one codepoint */
			m++;
			n = advance_cp(n);
		}
		else
		{
			if(fold_cmp_equal(m, n))
			{
				m = advance_cp(m);
				n = advance_cp(n);
			}
			else
			{
				if(!wild)
					return 0;
				m = ma;
				na = advance_cp(na);
				n = na;
			}
		}
	}
	return 0;
}

static int
match_esc_utf8(const char *mask, const char *name)
{
	const unsigned char *m = (const unsigned char *)mask;
	const unsigned char *n = (const unsigned char *)name;
	const unsigned char *ma = m;
	const unsigned char *na = n;
	int wild = 0;
	int calls = 0;
	int quote = 0;
	int match1 = 0;

	if(!mask || !name)
		return 0;

	if(*m == '*' && *(m + 1) == '\0')
		return 1;

	while(calls++ < MATCH_MAX_CALLS)
	{
		if(quote)
			quote++;
		if(quote == 3)
			quote = 0;
		if(*m == '\\' && !quote)
		{
			m++;
			quote = 1;
			continue;
		}
		if(!quote && *m == '*')
		{
			while(*m == '*')
				m++;
			wild = 1;
			ma = m;
			na = n;
			if(*m == '\\')
			{
				m++;
				if(!*m)
					return 0;
				quote++;
				continue;
			}
		}

		if(!*m)
		{
			if(!*n)
				return 1;
			if(quote)
				return 0;
			for(m--; (m > (const unsigned char *)mask) && (*m == '?'); m--)
				;
			if(*m == '*' && (m > (const unsigned char *)mask))
				return 1;
			if(!wild)
				return 0;
			m = ma;
			na = advance_cp(na);
			n = na;
		}
		else if(!*n)
		{
			if(quote)
				return 0;
			while(*m == '*')
				m++;
			return (*m == 0);
		}
		else
		{
			if(quote)
			{
				if(*m == 's')
					match1 = (*n == ' ');
				else
					match1 = fold_cmp_equal(m, n);
			}
			else if(*m == '?')
				match1 = 1;
			else if(*m == '@')
			{
				/* '@' matches one Unicode Letter */
				uint32_t cp;
				const unsigned char *tmp = n;
				if(*n < 0x80)
					match1 = IsLetter(*n);
				else if(utf8_decode(&tmp, &cp) >= 0)
					match1 = unicode_is_letter(cp);
				else
					match1 = 0;
			}
			else if(*m == '#')
			{
				/* '#' matches one Unicode Digit */
				uint32_t cp;
				const unsigned char *tmp = n;
				if(*n < 0x80)
					match1 = IsDigit(*n);
				else if(utf8_decode(&tmp, &cp) >= 0)
					match1 = unicode_is_digit(cp);
				else
					match1 = 0;
			}
			else
				match1 = fold_cmp_equal(m, n);

			if(match1)
			{
				m = advance_cp(m);
				n = advance_cp(n);
			}
			else
			{
				if(!wild)
					return 0;
				m = ma;
				na = advance_cp(na);
				n = na;
			}
		}
	}
	return 0;
}

/*
 * Skeleton hash table helpers for confusable detection.
 * These are called from m_nick.c alongside HASH_CLIENT operations.
 */
#include "client.h"

void
nick_skeleton_add(struct Client *client_p, const char *nick)
{
	char skel[NICKLEN * CASEFOLD_MAX_EXPANSION * 4];

	if(active_charset->normalize_nick == NULL)
		return;

	if(nick_compute_skeleton_utf8(nick, skel, sizeof(skel)) < 0)
		return;

	if(client_p->user->skeleton != NULL)
	{
		hash_del(HASH_SKELETON, client_p->user->skeleton, client_p);
		rb_free(client_p->user->skeleton);
	}

	client_p->user->skeleton = rb_strdup(skel);
	hash_add(HASH_SKELETON, client_p->user->skeleton, client_p);
}

void
nick_skeleton_del(struct Client *client_p)
{
	if(client_p->user == NULL || client_p->user->skeleton == NULL)
		return;

	hash_del(HASH_SKELETON, client_p->user->skeleton, client_p);
	rb_free(client_p->user->skeleton);
	client_p->user->skeleton = NULL;
}

struct Client *
nick_skeleton_find(const char *nick, struct Client *exclude)
{
	char skel[NICKLEN * CASEFOLD_MAX_EXPANSION * 4];

	if(active_charset->normalize_nick == NULL)
		return NULL;

	if(nick_compute_skeleton_utf8(nick, skel, sizeof(skel)) < 0)
		return NULL;

	rb_dlink_list *list = hash_find_list(HASH_SKELETON, skel);
	if(list == NULL)
		return NULL;

	rb_dlink_node *ptr;
	RB_DLINK_FOREACH(ptr, list->head)
	{
		struct Client *target = ptr->data;
		if(target != exclude)
		{
			hash_free_list(list);
			return target;
		}
	}
	hash_free_list(list);
	return NULL;
}

static int
utf8_normalize_nick(char *nick_buf, size_t buflen)
{
	return precis_prepare_nick_utf8(nick_buf, nick_buf, buflen);
}

/*
 * Permissive charset operations — activated by unicode_nicks / unicode_channels config.
 */
static struct charset_ops charset_permissive_ops = {
	.is_valid_nick_char = utf8_is_valid_nick_char,
	.is_valid_chan_char = utf8_is_valid_chan_char,
	.irc_cmp = utf8_irc_cmp,
	.hash_fold = utf8_hash_fold,
	.wild_match = match_utf8,
	.wild_match_esc = match_esc_utf8,
	.normalize_nick = utf8_normalize_nick,
	.casemapping_name = "utf-8",
	.charset_name = "utf-8",
};

/*
 * Mutable composite — when unicode_nicks and unicode_channels are
 * independently toggled, we mix function pointers from both strict
 * and permissive ops.
 */
static struct charset_ops charset_composite_ops;

struct charset_ops *active_charset = &charset_strict_ops;

void
charset_init(void)
{
	active_charset = &charset_strict_ops;
}

void
charset_apply_config(void)
{
	int want_nicks = ConfigFileEntry.unicode_nicks;
	int want_chans = ConfigFileEntry.unicode_channels;
	int was_unicode = (active_charset->irc_cmp != strict_irc_cmp);
	int want_unicode = (want_nicks || want_chans);

	if(!want_nicks && !want_chans)
	{
		/* Strict mode for everything */
		if(was_unicode)
		{
			ilog(L_MAIN, "Charset: switching to strict (ASCII/RFC1459) mode");
			active_charset = &charset_strict_ops;
			hash_rebuild_all_irccmp();
		}
		return;
	}

	/*
	 * Build a composite: pick nick/chan validators independently,
	 * but case comparison and hashing must be consistent (UTF-8
	 * if either unicode option is on).
	 */
	charset_composite_ops.is_valid_nick_char = want_nicks
		? utf8_is_valid_nick_char : strict_is_valid_nick_char;
	charset_composite_ops.is_valid_chan_char = want_chans
		? utf8_is_valid_chan_char : strict_is_valid_chan_char;
	charset_composite_ops.irc_cmp = utf8_irc_cmp;
	charset_composite_ops.hash_fold = utf8_hash_fold;
	charset_composite_ops.wild_match = match_utf8;
	charset_composite_ops.wild_match_esc = match_esc_utf8;
	charset_composite_ops.normalize_nick = want_nicks
		? utf8_normalize_nick : NULL;
	charset_composite_ops.casemapping_name = "utf-8";
	charset_composite_ops.charset_name = "utf-8";

	if(!was_unicode)
		ilog(L_MAIN, "Charset: switching to permissive (UTF-8) mode");

	active_charset = &charset_composite_ops;

	if(!was_unicode)
		hash_rebuild_all_irccmp();
}
