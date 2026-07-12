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

_SBOX: Final[list[int]] = []
_INV_SBOX: Final[list[int]] = []


def _init_sbox() -> None:
    if _SBOX:
        return
    p = q = 1
    sbox = [0] * 256
    # Generate the S-box using the standard multiplicative-inverse + affine map.
    while True:
        # p *= 3 in GF(2^8)
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        # q /= 3 (q *= 0xf6) via repeated xor-shifts
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        q ^= 0x09 if q & 0x80 else 0
        q &= 0xFF
        xformed = q ^ ((q << 1) & 0xFF) ^ ((q << 2) & 0xFF) ^ ((q << 3) & 0xFF) ^ ((q << 4) & 0xFF)
        sbox[p] = (xformed ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    _SBOX.extend(sbox)
    _INV_SBOX.extend(inv)


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
