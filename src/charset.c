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
#include "charset.h"
#include "match.h"

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

static uint32_t
strict_hash_fold(const unsigned char **s)
{
	return ToUpper(*(*s)++);
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
	.casemapping_name = "rfc1459",
	.charset_name = "ascii",
};

struct charset_ops *active_charset = &charset_strict_ops;

void
charset_init(void)
{
	active_charset = &charset_strict_ops;
}

void
charset_apply_config(void)
{
	/* Phase 4 will add unicode_nicks / unicode_channels config checks here.
	 * For now, always use strict mode. */
	active_charset = &charset_strict_ops;
}
