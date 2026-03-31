/*
 *  ircd-phatbox: An IRC daemon.
 *  charset.h: Character set abstraction for strict (ASCII/RFC1459) and
 *             permissive (Unicode/UTF-8) modes.
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

#ifndef INCLUDED_charset_h
#define INCLUDED_charset_h

#include <stdbool.h>
#include <stdint.h>

/*
 * struct charset_ops - strategy pattern for character set validation.
 *
 * Two implementations exist: strict (ASCII/RFC1459) and permissive (UTF-8).
 * The active implementation is selected at runtime via configuration.
 *
 * Functions that advance a pointer (is_valid_nick_char, is_valid_chan_char,
 * hash_fold) consume one logical character (1 byte in strict mode, 1-4 bytes
 * in permissive mode) and advance *p past it.
 */
struct charset_ops {
	/* Validate one nick character at *p. Advances *p on success. */
	bool (*is_valid_nick_char)(const char **p);

	/* Validate one channel name character at *p. Advances *p on success. */
	bool (*is_valid_chan_char)(const char **p);

	/* Case-insensitive string comparison. Returns 0 if equal. */
	int (*irc_cmp)(const char *s1, const char *s2);

	/* Case-fold and advance one character for hashing.
	 * Writes folded codepoint(s) to out (up to outmax).
	 * Returns count of codepoints written. */
	int (*hash_fold)(const unsigned char **s, uint32_t *out, int outmax);

	/* Case-insensitive wildcard match. Returns 1 on match, 0 otherwise. */
	int (*wild_match)(const char *mask, const char *name);

	/* Wildcard match with escape sequences. Returns 1 on match, 0 otherwise. */
	int (*wild_match_esc)(const char *mask, const char *name);

	/* ISUPPORT token values */
	const char *casemapping_name;	/* "rfc1459" or "utf-8" */
	const char *charset_name;	/* "ascii" or "utf-8" */
};

/* The currently active charset. Set by charset_init(), updated by charset_apply_config(). */
extern struct charset_ops *active_charset;

/* Initialise the charset subsystem. Must be called before any charset_ops use. */
void charset_init(void);

/* Re-read configuration and switch charset mode if needed. Called from rehash path. */
void charset_apply_config(void);

#endif /* INCLUDED_charset_h */
