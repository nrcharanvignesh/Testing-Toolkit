# Deterministic tests for kb/ modules: text extraction, archive safety,
# BM25, RRF fusion, crypto round-trip, file signatures, retriever availability.
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest


# --------------------------------------------------------------------------
# rrf_fuse
# --------------------------------------------------------------------------
def test_rrf_fuse_orders_by_consensus():
    from kb.retrieval import rrf_fuse

    # id "a" ranks high in both lists -> should win.
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]])
    assert fused[0] == "a"
    assert set(fused) == {"a", "b", "c"}


def test_rrf_fuse_top_n_and_empty():
    from kb.retrieval import rrf_fuse

    assert rrf_fuse([], top_n=5) == []
    assert rrf_fuse([["x", "y", "z"]], top_n=2) == ["x", "y"]


def test_rrf_fuse_stable_ties():
    from kb.retrieval import rrf_fuse

    # single list -> order preserved (ties broken by first appearance)
    assert rrf_fuse([["p", "q", "r"]]) == ["p", "q", "r"]


# --------------------------------------------------------------------------
# BM25
# --------------------------------------------------------------------------
def test_bm25_build_and_rank():
    from kb.bm25 import BM25Index

    ids = ["d1", "d2", "d3"]
    texts = [
        "the quick brown fox jumps",
        "a lazy dog sleeps all day",
        "quick foxes and quick dogs",
    ]
    idx = BM25Index.build(ids, texts)
    ranked = idx.top_n("quick fox", 3)
    assert ranked, "expected results"
    top_ids = [r[0] for r in ranked]
    # documents mentioning 'quick'/'fox' should outrank the lazy-dog doc
    assert "d2" != top_ids[0]


def test_bm25_roundtrip_dict():
    from kb.bm25 import BM25Index

    idx = BM25Index.build(["a", "b"], ["hello world", "world peace"])
    restored = BM25Index.from_dict(idx.to_dict())
    assert restored.top_n("world", 2)


def test_bm25_tokenize():
    from kb.bm25 import tokenize

    toks = tokenize("The Quick, Brown FOX!")
    assert "quick" in toks and "brown" in toks
    assert "the" not in toks  # stopword dropped by default


# --------------------------------------------------------------------------
# Retrieval-ready chunking and tunable authority metadata
# --------------------------------------------------------------------------
def test_structural_chunks_keep_heading_path_and_metadata():
    from kb.retrieval_config import RetrievalConfig
    from kb.store import chunk_document

    text = "# Product\n## Authentication\n" + ("Users sign in securely. " * 180)
    chunks = chunk_document(
        "guide.md", 0, text,
        config=RetrievalConfig(target_chunk_tokens=128, max_chunk_tokens=160, overlap_tokens=16),
        document_role="md", source_priority=0.9,
    )
    content_chunks = [chunk for chunk in chunks if chunk.section_path.endswith("Authentication")]
    assert len(content_chunks) > 1
    assert all(chunk.section_path == "Product > Authentication" for chunk in content_chunks)
    assert all(chunk.text.startswith("Document: guide.md\nSection: Product > Authentication") for chunk in content_chunks)
    assert all(chunk.source_priority == 0.9 for chunk in content_chunks)


def test_retrieval_config_is_neutral_and_project_tunable(tmp_path):
    import json
    from kb.retrieval_config import load_retrieval_config

    assert load_retrieval_config(tmp_path).priority_for("unknown.bin", "unknown") == 0.5
    (tmp_path / "kb_retrieval.json").write_text(json.dumps({
        "source_priorities": {"approved/*": 0.95},
        "fetch_k": 120,
        "final_k": 6,
    }), encoding="utf-8")
    config = load_retrieval_config(tmp_path)
    assert config.priority_for("approved/spec.md", "md") == 0.95
    assert config.fetch_k == 120 and config.final_k == 6


# --------------------------------------------------------------------------
# kb_crypto round-trip
# --------------------------------------------------------------------------
def test_kb_crypto_roundtrip():
    from kb import kb_crypto

    data = b"sensitive knowledge base bytes \x00\x01\x02"
    enc = kb_crypto.encrypt_bytes(data)
    assert enc != data
    assert kb_crypto.is_encrypted(enc)
    dec = kb_crypto.decrypt_bytes(enc)
    assert dec == data


def test_kb_crypto_file_roundtrip(tmp_path):
    from kb import kb_crypto

    p = tmp_path / "secret.bin"
    kb_crypto.write_encrypted_text(p, "hello secret")
    assert kb_crypto.read_decrypted_text(p) == "hello secret"


def test_kb_crypto_decrypt_garbage_is_safe():
    from kb import kb_crypto

    # decrypting non-encrypted bytes must not raise
    out = kb_crypto.decrypt_bytes(b"not-encrypted-plain")
    assert out is None or isinstance(out, bytes)


# --------------------------------------------------------------------------
# file signatures
# --------------------------------------------------------------------------
def test_file_sha_stable_and_cached(tmp_path):
    from kb.file_sig import file_sha

    f = tmp_path / "a.txt"
    f.write_text("content")
    cache: dict = {}
    s1 = file_sha(f, cache)
    s2 = file_sha(f, cache)  # served from cache
    assert s1 == s2 and len(s1) >= 16


def test_prune_hash_cache(tmp_path):
    from kb.file_sig import file_sha, prune_hash_cache

    a = tmp_path / "a.txt"; a.write_text("a")
    b = tmp_path / "b.txt"; b.write_text("b")
    cache: dict = {}
    file_sha(a, cache)
    file_sha(b, cache)
    prune_hash_cache(cache, [a])  # b no longer live
    # b's entry should be pruned; a retained. Entries nest under "entries",
    # keyed by str(path).
    keys = " ".join(cache.get("entries", {}))
    assert "a.txt" in keys
    assert "b.txt" not in keys


# --------------------------------------------------------------------------
# text extraction + archive safety
# --------------------------------------------------------------------------
def test_extract_text_plaintext(tmp_path):
    from kb.store import extract_text

    f = tmp_path / "note.txt"
    f.write_text("plain text content here")
    assert "plain text content" in extract_text(f)


def test_extract_text_never_raises_on_bad_file(tmp_path):
    from kb.store import extract_text

    f = tmp_path / "broken.pdf"
    f.write_bytes(b"\x00\x01not a real pdf")
    # must return '' rather than raising
    assert extract_text(f) == "" or isinstance(extract_text(f), str)


def test_extract_archive_reads_members(tmp_path):
    from kb.store import extract_text

    zpath = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("inside.txt", "archived body text")
    out = extract_text(zpath)
    assert "archived body text" in out


def test_extract_archive_zip_slip_guard(tmp_path):
    from kb.store import extract_text

    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../../escape.txt", "should not escape")
        zf.writestr("safe.txt", "safe body")
    # must not crash; traversal member skipped, safe member read
    out = extract_text(zpath)
    assert "safe body" in out
    # the escaped file must NOT have been written outside the temp dir
    assert not (tmp_path.parent / "escape.txt").exists()


def test_extract_archive_skips_nested(tmp_path):
    from kb.store import extract_text

    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z2:
        z2.writestr("deep.txt", "deep")
    zpath = tmp_path / "outer.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("nested.zip", inner.getvalue())
        zf.writestr("top.txt", "top level body")
    out = extract_text(zpath)
    assert "top level body" in out
    assert "deep" not in out  # nested archive skipped


def test_approx_tokens():
    from kb.store import approx_tokens

    # intentional floor of 1 (used for budgeting, never zero)
    assert approx_tokens("") == 1
    assert approx_tokens("hello world" * 100) > approx_tokens("hello world")


# --------------------------------------------------------------------------
# resumable file-delta indexing
# --------------------------------------------------------------------------
def test_index_refresh_only_processes_changed_file(tmp_path, monkeypatch):
    from kb import indexer

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "a.txt").write_text("alpha original", encoding="utf-8")
    (kb_dir / "b.txt").write_text("beta unchanged", encoding="utf-8")
    index_path = tmp_path / "kb_index.json"
    first = indexer.build_index_resumable(kb_dir, index_path)
    assert len(first.sources) == 2

    processed: list[str] = []
    original = indexer._process_one_file

    def tracked(path, *args, **kwargs):
        processed.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(indexer, "_process_one_file", tracked)
    (kb_dir / "a.txt").write_text("alpha updated", encoding="utf-8")
    second = indexer.build_index_resumable(kb_dir, index_path)

    assert processed == ["a.txt"]
    assert {source.name for source in second.sources} == {"a.txt", "b.txt"}
    assert any(chunk.doc == "b.txt" for chunk in second.chunks)


# --------------------------------------------------------------------------
# incremental project context
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_context_partial_is_returned_after_retries(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from kb import context_summary

    attempts: dict[str, int] = {}

    async def fake_extract(client, model, source, text):
        del client, model, text
        attempts[source] = attempts.get(source, 0) + 1
        if source == "bad.txt":
            raise TimeoutError("gateway unavailable")
        return context_summary.ProjectContext(
            actors=[context_summary.ContextItem("Buyer", "Purchases items", [source])]
        )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(context_summary, "_extract_window", fake_extract)
    monkeypatch.setattr(context_summary.asyncio, "sleep", no_sleep)
    index = SimpleNamespace(chunks=[
        SimpleNamespace(doc="good.txt", title="Good", context="", text="good"),
        SimpleNamespace(doc="bad.txt", title="Bad", context="", text="bad"),
    ])

    result = await context_summary.build_context_incremental_async(
        index, object(), "model", tmp_path, "fingerprint"
    )

    assert result.status == "partial"
    assert result.mapped_documents == 1
    assert result.total_documents == 2
    assert result.failed_documents == ["bad.txt"]
    assert attempts["bad.txt"] == 3
    assert (tmp_path / next(path.name for path in tmp_path.glob("*.json"))).exists()


# --------------------------------------------------------------------------
# HybridRetriever availability on an empty project
# --------------------------------------------------------------------------
def test_retriever_unavailable_when_no_index(tmp_path):
    from kb.retrieval import HybridRetriever

    r = HybridRetriever(tmp_path)
    # No index built -> not available, and must not raise.
    assert r.is_available() is False


# --------------------------------------------------------------------------
# embeddings L2 normalize
# --------------------------------------------------------------------------
def test_l2_normalize():
    from kb.embeddings import _l2_normalize

    mat = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = _l2_normalize(mat)
    # first row normalized to unit length
    assert abs(float(np.linalg.norm(out[0])) - 1.0) < 1e-5
    # zero row stays finite (no NaN)
    assert np.all(np.isfinite(out))
