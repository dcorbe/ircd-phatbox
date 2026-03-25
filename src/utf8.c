/*
 *  ircd-phatbox: An IRC daemon.
 *  utf8.c: UTF-8 encoding/decoding and validation.
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
#include "utf8.h"

/*
 * UTF-8 encoding:
 *   U+0000..U+007F     0xxxxxxx
 *   U+0080..U+07FF     110xxxxx 10xxxxxx
 *   U+0800..U+FFFF     1110xxxx 10xxxxxx 10xxxxxx
 *   U+10000..U+10FFFF  11110xxx 10xxxxxx 10xxxxxx 10xxxxxx
 *
 * We reject:
 *   - Overlong encodings (e.g. C0 80 for U+0000)
 *   - Surrogates (U+D800..U+DFFF)
 *   - Codepoints > U+10FFFF
 *   - Invalid lead/continuation bytes
 */

#define IS_CONT(b) (((b) & 0xC0) == 0x80)

int
utf8_decode(const unsigned char **p, uint32_t *cp)
{
	const unsigned char *s = *p;
	uint32_t c;
	int len;

	if(s[0] < 0x80)
	{
		*cp = s[0];
		*p = s + 1;
		return 1;
	}
	else if((s[0] & 0xE0) == 0xC0)
	{
		c = s[0] & 0x1F;
		len = 2;
	}
	else if((s[0] & 0xF0) == 0xE0)
	{
		c = s[0] & 0x0F;
		len = 3;
	}
	else if((s[0] & 0xF8) == 0xF0)
	{
		c = s[0] & 0x07;
		len = 4;
	}
	else
	{
		/* Invalid lead byte (0x80-0xBF, 0xF8-0xFF) */
		return -1;
	}

	for(int i = 1; i < len; i++)
	{
		if(!IS_CONT(s[i]))
			return -1;
		c = (c << 6) | (s[i] & 0x3F);
	}

	/* Reject overlong encodings */
	if(len == 2 && c < 0x80)
		return -1;
	if(len == 3 && c < 0x800)
		return -1;
	if(len == 4 && c < 0x10000)
		return -1;

	/* Reject surrogates */
	if(c >= 0xD800 && c <= 0xDFFF)
		return -1;

	/* Reject out-of-range */
	if(c > 0x10FFFF)
		return -1;

	*cp = c;
	*p = s + len;
	return len;
}

int
utf8_encode(uint32_t cp, unsigned char *buf)
{
	if(cp < 0x80)
	{
		buf[0] = (unsigned char)cp;
		return 1;
	}
	else if(cp < 0x800)
	{
		buf[0] = 0xC0 | (cp >> 6);
		buf[1] = 0x80 | (cp & 0x3F);
		return 2;
	}
	else if(cp < 0x10000)
	{
		/* Reject surrogates */
		if(cp >= 0xD800 && cp <= 0xDFFF)
			return -1;
		buf[0] = 0xE0 | (cp >> 12);
		buf[1] = 0x80 | ((cp >> 6) & 0x3F);
		buf[2] = 0x80 | (cp & 0x3F);
		return 3;
	}
	else if(cp <= 0x10FFFF)
	{
		buf[0] = 0xF0 | (cp >> 18);
		buf[1] = 0x80 | ((cp >> 12) & 0x3F);
		buf[2] = 0x80 | ((cp >> 6) & 0x3F);
		buf[3] = 0x80 | (cp & 0x3F);
		return 4;
	}

	return -1;
}

bool
utf8_validate(const char *s)
{
	const unsigned char *p = (const unsigned char *)s;
	uint32_t cp;

	while(*p)
	{
		if(utf8_decode(&p, &cp) < 0)
			return false;
	}

	return true;
}
