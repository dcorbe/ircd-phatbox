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

static void test_skeleton_utf8(void)
{
	char skel1[64], skel2[64];

	/* "hello" and "ℌello" (U+210C script H) should have the same skeleton
	 * (skeleton case-folds then applies confusable mapping) */
	TEST("skel utf8 hello", nick_compute_skeleton_utf8("hello", skel1, 64) == 0);
	TEST("skel utf8 ℌello", nick_compute_skeleton_utf8("\xE2\x84\x8C" "ello", skel2, 64) == 0);
	TEST("hello == ℌello skeleton", strcmp(skel1, skel2) == 0);

	/* Cyrillic а (U+0430) and Latin a have the same skeleton */
	TEST("skel utf8 Latin a", nick_compute_skeleton_utf8("a", skel1, 64) == 0);
	TEST("skel utf8 Cyrillic а", nick_compute_skeleton_utf8("\xD0\xB0", skel2, 64) == 0);
	TEST("Latin a == Cyrillic а skeleton", strcmp(skel1, skel2) == 0);

	/* Different nicks have different skeletons */
	TEST("skel utf8 foo", nick_compute_skeleton_utf8("foo", skel1, 64) == 0);
	TEST("skel utf8 bar", nick_compute_skeleton_utf8("bar", skel2, 64) == 0);
	TEST("foo != bar skeleton", strcmp(skel1, skel2) != 0);
}

static void test_precis_utf8(void)
{
	char out[64];

	/* ASCII passthrough — case-folded */
	TEST("PRECIS UTF-8 ASCII", precis_prepare_nick_utf8("Hello", out, 64) == 0);
	TEST("PRECIS UTF-8 ASCII folded", strcmp(out, "hello") == 0);

	/* NFC normalization: e + combining acute (NFD) → é (NFC), then case-folded */
	TEST("PRECIS UTF-8 NFD", precis_prepare_nick_utf8("Caf\x65\xCC\x81", out, 64) == 0);
	/* Should produce "café" (case-folded c + NFC é) */
	TEST("PRECIS UTF-8 NFD result", strcmp(out, "caf\xC3\xA9") == 0);

	/* Width mapping: fullwidth A (U+FF21) → a (case-folded) */
	TEST("PRECIS UTF-8 width", precis_prepare_nick_utf8("\xEF\xBC\xA1", out, 64) == 0);
	TEST("PRECIS UTF-8 width result", strcmp(out, "a") == 0);

	/* Invalid UTF-8 rejected */
	TEST("PRECIS UTF-8 reject bad", precis_prepare_nick_utf8("bad\xFF", out, 64) == -1);
}

static void test_bidi_class(void)
{
	/* ASCII: Latin → BIDI_L */
	TEST("bidi 'A' is L", unicode_bidi_class(0x41) == BIDI_L);

	/* Hebrew Alef (U+05D0) → BIDI_R */
	TEST("bidi Hebrew Alef is R", unicode_bidi_class(0x05D0) == BIDI_R);

	/* Arabic Alif (U+0627) → BIDI_AL */
	TEST("bidi Arabic Alif is AL", unicode_bidi_class(0x0627) == BIDI_AL);

	/* Syriac Alaph (U+0710) → BIDI_AL — broken with old hardcoded stub */
	TEST("bidi Syriac Alaph is AL", unicode_bidi_class(0x0710) == BIDI_AL);

	/* Thaana (U+0780) → BIDI_AL */
	TEST("bidi Thaana is AL", unicode_bidi_class(0x0780) == BIDI_AL);

	/* N'Ko (U+07C0) → BIDI_R */
	TEST("bidi N'Ko is R", unicode_bidi_class(0x07C0) == BIDI_R);

	/* European digit (U+0030 '0') → BIDI_EN */
	TEST("bidi digit '0' is EN", unicode_bidi_class(0x30) == BIDI_EN);

	/* Arabic-Indic digit (U+0660) → BIDI_AN */
	TEST("bidi Arabic-Indic 0 is AN", unicode_bidi_class(0x0660) == BIDI_AN);

	/* Combining acute (U+0301) → BIDI_NSM */
	TEST("bidi combining acute is NSM", unicode_bidi_class(0x0301) == BIDI_NSM);

	/* CJK ideograph (U+4E00) → BIDI_L */
	TEST("bidi CJK is L", unicode_bidi_class(0x4E00) == BIDI_L);
}

static void test_full_case_folding(void)
{
	uint32_t out[CASEFOLD_MAX_EXPANSION];
	int n;

	/* Simple fold: 'A' -> 'a' (1 codepoint) */
	n = unicode_casefold_full(0x41, out, CASEFOLD_MAX_EXPANSION);
	TEST("full fold 'A' -> 'a' count", n == 1);
	TEST("full fold 'A' -> 'a' value", out[0] == 0x61);

	/* Already lowercase: 'a' unchanged */
	n = unicode_casefold_full(0x61, out, CASEFOLD_MAX_EXPANSION);
	TEST("full fold 'a' unchanged count", n == 1);
	TEST("full fold 'a' unchanged value", out[0] == 0x61);

	/* Full fold: ß (U+00DF) -> ss (2 codepoints) */
	n = unicode_casefold_full(0x00DF, out, CASEFOLD_MAX_EXPANSION);
	TEST("full fold ß count", n == 2);
	TEST("full fold ß [0]=s", out[0] == 0x73);
	TEST("full fold ß [1]=s", out[1] == 0x73);

	/* Full fold: fi ligature (U+FB01) -> fi (2 codepoints) */
	n = unicode_casefold_full(0xFB01, out, CASEFOLD_MAX_EXPANSION);
	TEST("full fold fi ligature count", n == 2);
	TEST("full fold fi ligature [0]=f", out[0] == 0x66);
	TEST("full fold fi ligature [1]=i", out[1] == 0x69);

	/* Cyrillic А (U+0410) -> а (U+0430), simple fold path */
	n = unicode_casefold_full(0x0410, out, CASEFOLD_MAX_EXPANSION);
	TEST("full fold Cyrillic А count", n == 1);
	TEST("full fold Cyrillic А value", out[0] == 0x0430);

	/* CJK unchanged */
	n = unicode_casefold_full(0x592A, out, CASEFOLD_MAX_EXPANSION);
	TEST("full fold CJK unchanged count", n == 1);
	TEST("full fold CJK unchanged value", out[0] == 0x592A);

	/* Buffer too small for ß */
	n = unicode_casefold_full(0x00DF, out, 1);
	TEST("full fold ß buffer too small", n == -1);
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
	test_precis_utf8();
	test_skeleton_utf8();
	test_bidi_class();
	test_full_case_folding();
	test_skeleton();

	printf("\n%d/%d tests passed\n", tests_passed, tests_run);
	return (tests_passed == tests_run) ? 0 : 1;
}
