/*
 * Quick integration test for charset UTF-8 functions.
 * Build: gcc -I../include -I../libratbox/include -o test_charset test_charset.c \
 *        ../src/.libs/libcore.a ../libratbox/src/.libs/libratbox.a -lz -lssl -lcrypto
 * Or just link against the dylib.
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>

/* Pull in the public headers directly */
#include "utf8.h"
#include "unicode_data.h"

static int tests_run = 0;
static int tests_passed = 0;

#define TEST(name, expr) do { \
	tests_run++; \
	if(expr) { tests_passed++; } \
	else { printf("FAIL: %s\n", name); } \
} while(0)

static void test_utf8_decode(void)
{
	const unsigned char *p;
	uint32_t cp;

	/* ASCII */
	p = (const unsigned char *)"A";
	assert(utf8_decode(&p, &cp) == 1);
	TEST("decode ASCII 'A'", cp == 0x41);

	/* 2-byte: é = U+00E9 = C3 A9 */
	p = (const unsigned char *)"\xC3\xA9";
	assert(utf8_decode(&p, &cp) == 2);
	TEST("decode U+00E9 (é)", cp == 0x00E9);

	/* 3-byte: 太 = U+592A = E5 A4 AA */
	p = (const unsigned char *)"\xE5\xA4\xAA";
	assert(utf8_decode(&p, &cp) == 3);
	TEST("decode U+592A (太)", cp == 0x592A);

	/* 4-byte: 𝄞 = U+1D11E = F0 9D 84 9E */
	p = (const unsigned char *)"\xF0\x9D\x84\x9E";
	assert(utf8_decode(&p, &cp) == 4);
	TEST("decode U+1D11E (𝄞)", cp == 0x1D11E);

	/* Reject overlong 2-byte NUL */
	p = (const unsigned char *)"\xC0\x80";
	TEST("reject overlong C0 80", utf8_decode(&p, &cp) == -1);

	/* Reject surrogate */
	p = (const unsigned char *)"\xED\xA0\x80";
	TEST("reject surrogate ED A0 80", utf8_decode(&p, &cp) == -1);

	/* Reject truncated */
	p = (const unsigned char *)"\xC3";
	TEST("reject truncated C3", utf8_decode(&p, &cp) == -1);
}

static void test_utf8_validate(void)
{
	TEST("validate ASCII", utf8_validate("hello"));
	TEST("validate UTF-8 é", utf8_validate("caf\xC3\xA9"));
	TEST("validate empty", utf8_validate(""));
	TEST("reject invalid byte", !utf8_validate("bad\xFF"));
	TEST("reject truncated", !utf8_validate("bad\xC3"));
}

static void test_unicode_categories(void)
{
	TEST("'A' is letter", unicode_is_letter(0x41));
	TEST("'z' is letter", unicode_is_letter(0x7A));
	TEST("Cyrillic Б is letter", unicode_is_letter(0x0411));
	TEST("CJK 太 is letter", unicode_is_letter(0x592A));
	TEST("'5' is not letter", !unicode_is_letter(0x35));

	TEST("'0' is digit", unicode_is_digit(0x30));
	TEST("Arabic-Indic 0 is digit", unicode_is_digit(0x0660));
	TEST("'A' is not digit", !unicode_is_digit(0x41));

	TEST("combining acute is mark", unicode_is_mark(0x0301));
	TEST("'A' is not mark", !unicode_is_mark(0x41));
}

static void test_case_folding(void)
{
	TEST("fold 'A' -> 'a'", unicode_casefold(0x41) == 0x61);
	TEST("fold 'Z' -> 'z'", unicode_casefold(0x5A) == 0x7A);
	TEST("fold 'a' unchanged", unicode_casefold(0x61) == 0x61);
	TEST("fold Cyrillic А -> а", unicode_casefold(0x0410) == 0x0430);
	TEST("fold CJK unchanged", unicode_casefold(0x592A) == 0x592A);
}

static void test_nfc(void)
{
	/* é composed (U+00E9) vs decomposed (U+0065 U+0301) */
	uint32_t decomposed[] = {0x0065, 0x0301}; /* e + combining acute */
	uint32_t output[8];
	int len = unicode_nfc(decomposed, 2, output, 8);
	TEST("NFC e+acute -> é", len == 1 && output[0] == 0x00E9);

	/* Already NFC */
	uint32_t composed[] = {0x00E9};
	len = unicode_nfc(composed, 1, output, 8);
	TEST("NFC é stays é", len == 1 && output[0] == 0x00E9);

	/* ASCII passthrough */
	uint32_t ascii[] = {0x48, 0x65, 0x6C, 0x6C, 0x6F};
	len = unicode_nfc(ascii, 5, output, 8);
	TEST("NFC ASCII passthrough", len == 5 && output[0] == 0x48);
}

static void test_script(void)
{
	TEST("'A' is Latin", unicode_script(0x41) != SCRIPT_COMMON);
	TEST("'0' is Common", unicode_script(0x30) == SCRIPT_COMMON);
	TEST("Cyrillic Б script differs from Latin", unicode_script(0x0411) != unicode_script(0x41));
}

static void test_precis(void)
{
	uint32_t output[64];

	/* Simple ASCII */
	int len = precis_prepare_nick("Hello", output, 64);
	TEST("PRECIS ASCII nick", len > 0);

	/* UTF-8 with accents */
	len = precis_prepare_nick("Caf\xC3\xA9", output, 64);
	TEST("PRECIS accented nick", len > 0);

	/* Invalid UTF-8 */
	len = precis_prepare_nick("bad\xFF", output, 64);
	TEST("PRECIS rejects invalid UTF-8", len == -1);
}

static void test_skeleton(void)
{
	uint32_t in1[] = {0x48, 0x65, 0x6C, 0x6C, 0x6F}; /* Hello */
	uint32_t out1[64], out2[64];
	int len1 = unicode_skeleton(in1, 5, out1, 64);
	TEST("skeleton Hello", len1 > 0);

	/* ℌello (U+210C = script H) should have same skeleton as Hello */
	uint32_t in2[] = {0x210C, 0x65, 0x6C, 0x6C, 0x6F};
	int len2 = unicode_skeleton(in2, 5, out2, 64);
	TEST("skeleton ℌello", len2 > 0);

	if(len1 > 0 && len2 > 0)
	{
		int same = (len1 == len2) && (memcmp(out1, out2, len1 * sizeof(uint32_t)) == 0);
		TEST("Hello and ℌello have same skeleton", same);
	}
}

int main(void)
{
	test_utf8_decode();
	test_utf8_validate();
	test_unicode_categories();
	test_case_folding();
	test_nfc();
	test_script();
	test_precis();
	test_skeleton();

	printf("\n%d/%d tests passed\n", tests_passed, tests_run);
	return (tests_passed == tests_run) ? 0 : 1;
}
