"""Pure-Python AES-256-GCM decrypt used only when the native ``cryptography``
binding is unavailable or broken on the host (a real failure mode on some
locked-down Windows machines).

This exists so the centrally managed AI credential can always be decrypted on
the client, regardless of whether the compiled ``cryptography`` wheel loads.
It is intentionally dependency-free (stdlib only) and is cross-validated
against ``cryptography`` in the test suite on every build.

Correctness, not speed, is the goal: the envelope is a few hundred bytes and is
decrypted once at startup, so a pure-Python implementation is more than fast
enough. The output is byte-for-byte identical to ``cryptography``'s AESGCM.
"""
from __future__ import annotations

from typing import Final

# ---- AES (FIPS-197) ---------------------------------------------------------

# Canonical AES (FIPS-197) S-box as a constant table. Using the published
# constant avoids any chance of a bug in on-the-fly S-box generation.
_SBOX: Final[tuple[int, ...]] = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
    0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
    0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
    0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
    0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
    0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
    0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
    0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
    0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
    0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
    0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
)


def _init_sbox() -> None:
    """Retained as a no-op for API compatibility; the S-box is a constant."""
    return


_RCON: Final[tuple[int, ...]] = (
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36,
    0x6C, 0xD8, 0xAB, 0x4D,
)


def _xtime(a: int) -> int:
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _mul(a: int, b: int) -> int:
    """Multiply two bytes in GF(2^8)."""
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        b >>= 1
        a = _xtime(a)
    return result & 0xFF


class _AES256:
    """Minimal AES-256 block cipher (encrypt-only; GCM never uses decrypt)."""

    def __init__(self, key: bytes) -> None:
        _init_sbox()
        if len(key) != 32:
            raise ValueError("AES-256 requires a 32-byte key")
        self._rk = self._expand_key(key)

    @staticmethod
    def _expand_key(key: bytes) -> list[list[int]]:
        nk, nr = 8, 14
        words = [list(key[4 * i : 4 * i + 4]) for i in range(nk)]
        for i in range(nk, 4 * (nr + 1)):
            temp = list(words[i - 1])
            if i % nk == 0:
                temp = temp[1:] + temp[:1]  # RotWord
                temp = [_SBOX[b] for b in temp]  # SubWord
                temp[0] ^= _RCON[i // nk - 1]
            elif i % nk == 4:
                temp = [_SBOX[b] for b in temp]
            words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
        # Group words into 16-byte round keys.
        return [
            [b for w in words[4 * r : 4 * r + 4] for b in w]
            for r in range(nr + 1)
        ]

    def encrypt_block(self, block: bytes) -> bytes:
        # State bytes are in input order s[0..15]; byte i is row i%4, col i//4.
        s = list(block)
        self._add_round_key(s, self._rk[0])
        for rnd in range(1, 14):
            self._sub_bytes(s)
            self._shift_rows(s)
            self._mix_columns(s)
            self._add_round_key(s, self._rk[rnd])
        self._sub_bytes(s)
        self._shift_rows(s)
        self._add_round_key(s, self._rk[14])
        return bytes(s)

    @staticmethod
    def _add_round_key(s: list[int], rk: list[int]) -> None:
        for i in range(16):
            s[i] ^= rk[i]

    @staticmethod
    def _sub_bytes(s: list[int]) -> None:
        for i in range(16):
            s[i] = _SBOX[s[i]]

    @staticmethod
    def _shift_rows(s: list[int]) -> None:
        # Column-major layout: element (row, col) is at index (col*4 + row).
        # Row r is cyclically left-shifted by r.
        new = list(s)
        for r in range(4):
            for c in range(4):
                new[c * 4 + r] = s[((c + r) % 4) * 4 + r]
        s[:] = new

    @staticmethod
    def _mix_columns(s: list[int]) -> None:
        for c in range(4):
            i = c * 4
            a0, a1, a2, a3 = s[i], s[i + 1], s[i + 2], s[i + 3]
            s[i + 0] = _mul(a0, 2) ^ _mul(a1, 3) ^ a2 ^ a3
            s[i + 1] = a0 ^ _mul(a1, 2) ^ _mul(a2, 3) ^ a3
            s[i + 2] = a0 ^ a1 ^ _mul(a2, 2) ^ _mul(a3, 3)
            s[i + 3] = _mul(a0, 3) ^ a1 ^ a2 ^ _mul(a3, 2)


# ---- GCM (NIST SP 800-38D) --------------------------------------------------

def _bytes_to_int(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _int_to_block(x: int) -> bytes:
    return (x & ((1 << 128) - 1)).to_bytes(16, "big")


def _gf_mult(x: int, y: int) -> int:
    """Multiply in GF(2^128) with the GCM reduction polynomial."""
    z = 0
    v = y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ (0xE1 << 120)
        else:
            v >>= 1
    return z


def _ghash(h: int, data: bytes) -> int:
    y = 0
    for i in range(0, len(data), 16):
        block = data[i : i + 16]
        if len(block) < 16:
            block = block + b"\x00" * (16 - len(block))
        y = _gf_mult(y ^ _bytes_to_int(block), h)
    return y


def _inc32(block: bytes) -> bytes:
    prefix, ctr = block[:12], _bytes_to_int(block[12:])
    return prefix + ((ctr + 1) & 0xFFFFFFFF).to_bytes(4, "big")


def decrypt(key: bytes, nonce: bytes, ciphertext_and_tag: bytes, aad: bytes) -> bytes:
    """AES-256-GCM decrypt+verify. Raises ValueError on auth failure.

    ``ciphertext_and_tag`` is the ciphertext with the 16-byte tag appended, i.e.
    the exact output layout of ``cryptography``'s ``AESGCM.encrypt``.
    """
    if len(nonce) != 12:
        raise ValueError("GCM nonce must be 12 bytes")
    if len(ciphertext_and_tag) < 16:
        raise ValueError("ciphertext too short")
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    aes = _AES256(key)
    h = _bytes_to_int(aes.encrypt_block(b"\x00" * 16))
    j0 = nonce + b"\x00\x00\x00\x01"

    # Verify the authentication tag before releasing any plaintext.
    lengths = (len(aad) * 8).to_bytes(8, "big") + (len(ciphertext) * 8).to_bytes(8, "big")
    ghash_in = (
        aad + b"\x00" * ((-len(aad)) % 16)
        + ciphertext + b"\x00" * ((-len(ciphertext)) % 16)
        + lengths
    )
    s = _ghash(h, ghash_in)
    expected_tag = _int_to_block(s ^ _bytes_to_int(aes.encrypt_block(j0)))
    # Constant-time comparison.
    diff = 0
    for a, b in zip(expected_tag, tag):
        diff |= a ^ b
    if diff != 0 or len(expected_tag) != len(tag):
        raise ValueError("GCM authentication tag mismatch")

    # GCTR decryption starting from inc32(J0).
    out = bytearray()
    counter = _inc32(j0)
    for i in range(0, len(ciphertext), 16):
        keystream = aes.encrypt_block(counter)
        block = ciphertext[i : i + 16]
        out.extend(bytes(x ^ y for x, y in zip(block, keystream)))
        counter = _inc32(counter)
    return bytes(out)
