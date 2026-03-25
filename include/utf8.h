/*
 *  ircd-phatbox: An IRC daemon.
 *  utf8.h: UTF-8 encoding/decoding and validation.
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

#ifndef INCLUDED_utf8_h
#define INCLUDED_utf8_h

#include <stdbool.h>
#include <stdint.h>

/*
 * utf8_decode - decode one codepoint from a UTF-8 byte sequence.
 *
 * On success, writes the codepoint to *cp, advances *p past the
 * consumed bytes, and returns the number of bytes consumed (1-4).
 *
 * On failure (invalid lead byte, truncated sequence, overlong encoding,
 * surrogate, or codepoint > U+10FFFF), returns -1 and does not advance *p.
 */
int utf8_decode(const unsigned char **p, uint32_t *cp);

/*
 * utf8_encode - encode one codepoint as UTF-8.
 *
 * Writes 1-4 bytes to buf (which must have room for at least 4 bytes).
 * Returns the number of bytes written, or -1 if cp is not a valid
 * Unicode scalar value (surrogates or > U+10FFFF).
 */
int utf8_encode(uint32_t cp, unsigned char *buf);

/*
 * utf8_validate - check whether an entire NUL-terminated string is
 *                 well-formed UTF-8.
 */
bool utf8_validate(const char *s);

#endif /* INCLUDED_utf8_h */
