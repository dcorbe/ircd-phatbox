# ircd-phatbox

A modern IRC daemon forked from [ircd-ratbox](https://github.com/irc-archive/ratbox-mirror), built on the security-hardened [mannfred fork](https://github.com/mannfredcom/ircd-ratbox).

## What is this?

ircd-phatbox is an IRC server (IRCd) that continues the ircd-ratbox lineage with a focus on modern toolchains, security, and maintainability. It compiles cleanly with C23 on current systems and drops legacy platform baggage.

## Key changes from upstream

**Build modernization**
- Builds with C23 (`-std=gnu23`) on modern clang and gcc
- Fixed K&R function pointer types that C23 rejects
- Autotools regenerated for automake 1.18+ / libtool 2.5+
- SSL include paths properly wired through pkg-config

**Security hardening** (inherited from mannfred)
- FNV hash folding bug fixed (bitwise XOR vs exponentiation — broken since original implementation)
- DNS resolver overhauled: 32-bit IDs, hash-based lookup, use-after-free fix in callbacks
- Close-on-exec enforced on all file descriptors
- D-line/K-line input validation and CIDR range checks
- SSL/TLS state machine fixes (connection leak, error path handling)
- Buffer length validation in linebuf

**Platform changes** (inherited from mannfred)
- Windows support removed entirely
- mbedTLS added as third SSL backend (alongside OpenSSL and GnuTLS)
- TLS 1.3 support
- Thread-safe `gmtime_r()` used unconditionally
- Hybrid 5 era protocol artifacts removed

## Building

```sh
./configure --prefix=/usr/local/ircd-phatbox
make
make install
```

### Requirements

- C99-capable compiler (C23 recommended)
- An SSL library: OpenSSL, LibreSSL, GnuTLS, or mbedTLS
- GNU autotools (autoconf, automake, libtool) if building from git
- zlib (optional, for compressed server links)
- flex/lex

### Supported platforms

- Linux (glibc)
- macOS (Apple Silicon and Intel)
- FreeBSD
- Other POSIX systems likely work but are untested

## Configuration

See `doc/example.conf` for a complete configuration reference. The config format is the same as ircd-ratbox 3.x.

If upgrading from ircd-ratbox, read `doc/whats-new-3.0.txt`.

## Lineage

```
ircd-hybrid → ircd-ratbox (2002-2017) → mannfred fork (2026) → ircd-phatbox
```

ircd-phatbox 3.2.0 is based on the mannfred fork's main branch, which builds on the ircd-ratbox 3.1-dev trunk with ~200 additional commits of security hardening, bug fixes, and modernization.

## License

GNU General Public License v2. See `LICENSE` for details.

## Credits

See `CREDITS` for the full list of contributors to ircd-ratbox and its ancestors. ircd-phatbox builds on decades of work by the Hybrid, ircd-ratbox, and mannfred development teams.
