> **Historical desktop reference:** This document describes the legacy native
> desktop build and local-model architecture. It is retained for parity research,
> not as installation, security, model-routing, or credential guidance for the
> current Vercel web + local-agent product. Use `README.md`, `docs/ARCHITECTURE.md`,
> `docs/DOCS.md`, and `agent-bundle/INSTALL.md` for current behavior.

## Deployment & Installation

### Portable Zero-Install (Recommended)

On a connected machine (Machine A):

```bash
cd src
python make_portable.py              # downloads portable Python 3.12
python make_wheelhouse.py            # downloads all dependency wheels
```

On the target machine (Machine B - no Python, no pip, no network):

```
Double-click install.cmd (Windows) or run ./install.sh (macOS/Linux)
```

Every run: cleans old builds, installs packages, builds .exe, launches (~2-3 min).
No caching or skip logic - always a guaranteed clean build.

### What install.cmd Does (Windows, native cmd.exe)

1. Patches `python312._pth` (ensures pip + app imports work)
2. Cleans old `build/`, `dist/`, `__pycache__/`
3. Installs all packages from `src/wheelhouse/` (offline)
4. Installs PyInstaller from wheelhouse
5. Runs `build.py --quiet` (progress bar only)
6. Launches built `src/dist/TestingToolkit/TestingToolkit.exe`

### Critical Constraints

- `install.cmd` is native cmd.exe ONLY (NO Git Bash - Qt SEGFAULTS in mintty)
- `python312._pth` must contain: `python312.zip`, `.`, `..\src`, `Lib\site-packages`, `import site`
- Wheels must be cp312 (matching python-embed 3.12.9)
- PyInstaller wheel must be in wheelhouse (build-time dep)
- No sentinel file - always reinstalls fresh

### Supported Platforms

| Platform | Arch | Python Source |
| -------- | ---- | ------------- |
| Windows | x64, ARM64 | python.org embeddable zip |
| macOS | Intel, Apple Silicon (M1-M4) | python-build-standalone |
| Linux | x64 | python-build-standalone |

### Files

| File | Purpose |
| ---- | ------- |
| `install.cmd` | Windows native launcher (cmd.exe, builds + launches .exe) |
| `install.sh` | macOS/Linux launcher (builds + launches app) |
| `src/make_portable.py` | Downloads portable Python distribution |
| `src/make_wheelhouse.py` | Downloads all wheels for offline install |
| `src/build.py` | PyInstaller build pipeline (called by installers) |

---

## Local Models, Indexing & Search — Technical Overview

### The Stack at a Glance

| Component                | Model / Tech                         | Size   | Format        | Library    |
| ------------------------ | ------------------------------------ | ------ | ------------- | ---------- |
| **Embeddings**     | BAAI/bge-small-en-v1.5               | ~32 MB | INT8 ONNX     | fastembed  |
| **Reranker**       | Xenova/ms-marco-MiniLM-L-6-v2        | ~22 MB | INT8 ONNX     | fastembed  |
| **Lexical Search** | Okapi BM25 (k1=1.5, b=0.75)          | —     | NumPy arrays  | Pure NumPy |
| **Vector Store**   | LanceDB (primary) / NumPy (fallback) | —     | Memory-mapped | LanceDB    |

All components are **CPU-only, fully offline, no GPU, no network at query time.**

---

### 1. Embedding Model

* **bge-small-en-v1.5** — a 384-dimensional text embedding model from BAAI
* Quantized to INT8 ONNX (32 MB vs 130 MB full-precision)
* Loaded via `fastembed.TextEmbedding` with `local_files_only=True`
* Processes 64 texts per batch, outputs L2-normalized float32 vectors
* Cosine similarity = dot product (since vectors are unit-normalized)
* Bundled in `models/` folder — never downloads at runtime

### 2. Reranker (Cross-Encoder)

* **ms-marco-MiniLM-L-6-v2** — a cross-encoder trained on MS-MARCO passage ranking
* Takes (query, candidate_passage) pairs and scores relevance directly
* Much more precise than embeddings alone (sees both texts simultaneously)
* INT8 ONNX, ~22 MB, scores 96 candidates in tens of milliseconds on your U-series CPU
* Applied after RRF fusion to pick the final 32 best chunks

### 3. BM25 (Lexical Search)

* Classic Okapi BM25 scoring: `score = Σ IDF(t) × (tf × (k1+1)) / (tf + k1 × (1 - b + b × dl/avgdl))`
* Tokenization: lowercase alphanumeric, 44 English stop words (kept conservative to preserve codes/identifiers)
* Persisted as `bm25.json` — instant reload, no rebuild needed
* Excellent for exact identifiers, field names, error codes, system-specific terms

### 4. Indexing Pipeline

```
Documents (PDF, DOCX, XLSX, etc.)
  → Extract text (format-specific extractors + OCR for scanned pages)
  → Coarse chunks (~6,000 tokens, split on headings/paragraphs)
  → Dense chunks (~550 tokens with 70-token overlap, for retrieval precision)
  → BM25 index (always built)
  → Dense vectors (if fastembed available, 384-dim float32)
  → Persist to disk (kb_index.json + bm25.json + vectors.npy)
```

Stable deterministic IDs: `d003c0007#2` = document 3, coarse chunk 7, dense sub-chunk 2.
Index rebuilds only when files change (mtime/size check).

### 5. Search Pipeline (at query time)

```
Query (work item dump text)
  │
  ├─ BM25 → top 96 lexical candidates (ranked by term overlap)
  │
  ├─ Dense search → top 96 semantic candidates (cosine similarity)
  │
  ├─ RRF Fusion: score = Σ 1/(60 + rank), merge both lists
  │
  ├─ Cross-encoder rerank: rescore top 96 → pick best 32
  │
  └─ Deduplicate (signature on first 240 chars) → final 32 chunks
```

**Graceful degradation:**

* No embedder → BM25-only (still effective for exact terms)
* No reranker → RRF-fused order (still combines lexical + semantic)
* No vector store → full fallback to RLM recursive navigation (LLM-guided)

### 6. Why Two Search Engines?

| Query Type                 | BM25 Wins        | Dense Wins                                         |
| -------------------------- | ---------------- | -------------------------------------------------- |
| "SSN validation field"     | ✓ (exact match) |                                                    |
| "user authentication flow" |                  | ✓ (semantic match on "login", "credential entry") |
| "E28 enhancement grid"     | ✓ (exact code)  |                                                    |
| "what happens on timeout"  |                  | ✓ (finds "session expiry" content)                |

RRF fusion captures both — you get the precision of keyword matching AND the recall of semantic understanding.

### 7. Performance	

| Operation                         | Time            |
| --------------------------------- | --------------- |
| Embed 1 query (384-dim)           | ~15ms           |
| BM25 search (1000 chunks)         | ~2ms            |
| Dense vector search (1000 chunks) | ~5ms            |
| Rerank 96 candidates              | ~30ms           |
| **Total retrieval**         | **~50ms** |

All under 100ms. The only slow part is the final Claude API call for generation (~15s).



## How the Local Smart Search Works

**work item details + system prompt (per phase) + relevant context from local smart search → sent to Claude → test cases back.**

The "local smart search" is the middle piece — here's how it works in plain terms:

### What Problem It Solves

You might have 200 pages of requirement docs. Claude's context window can only take ~25k characters of context. So you can't just dump everything in. The app needs to **figure out which 5-10 paragraphs (out of hundreds) are actually relevant** to the work items you selected.

### How It Works (Two Search Engines Working Together)

**Engine 1: Keyword Search (BM25)**

* Works like Ctrl+F on steroids across all your documents
* Finds exact matches: field names, error codes, feature names, specific terms
* Example: work item says "SSN validation" → finds every paragraph mentioning "SSN"

**Engine 2: Meaning Search (Dense Vectors)**

* Understands concepts, not just words
* If the work item says "user login" it can find paragraphs about "authentication flow" or "credential entry" even if they never say "login"
* Uses a small AI model running locally on your CPU (no API call needed)

**Then: Combine + Re-rank**

* Both engines' results are merged (Reciprocal Rank Fusion)
* A third local model (cross-encoder) re-scores the top candidates to pick the absolute best matches
* Final result: the ~12 most relevant paragraphs from your entire KB

### The Key Point for Your Demo

All of this runs  **instantly, locally, with no API calls** . The only thing that touches the internet is the final generation call to Claude. So:

* Document indexing = local, one-time (rebuilds only when docs change)
* Searching for relevant context = local, sub-second
* Generating test cases = API call to Claude (the only network cost)

This means you're only paying for the generation — the "smart" part of finding what's relevant is free and fast.
