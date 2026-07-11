> **Historical desktop parity snapshot:** not current web-agent security or
> installation guidance. See `../ARCHITECTURE.md` and `../../agent-bundle/INSTALL.md`.

# Testing Toolkit - Architecture Reference

## Models

| Task | Model ID | Provider | Tier | Cost (in/out per 1M) |
|------|----------|----------|------|---------------------|
| TC Generation | bedrock.anthropic.claude-opus-4-6 | Anthropic (Bedrock) | Frontier | $15 / $75 |
| Coverage Verify | bedrock.anthropic.claude-opus-4-6 | Anthropic (Bedrock) | Frontier | $15 / $75 |
| Template Analysis | bedrock.anthropic.claude-opus-4-6 | Anthropic (Bedrock) | Frontier | $15 / $75 |
| Decomposition | bedrock.anthropic.claude-opus-4-6 | Anthropic (Bedrock) | Frontier | $15 / $75 |
| Chat / Streaming | bedrock.anthropic.claude-sonnet-4-6 | Anthropic (Bedrock) | Medium | $3 / $15 |
| Navigation | bedrock.anthropic.claude-sonnet-4-6 | Anthropic (Bedrock) | Medium | $3 / $15 |
| Extraction | azure.gpt-4o | Azure OpenAI | Medium | $2.50 / $10 |
| Defect Parsing | azure.gpt-4o | Azure OpenAI | Medium | $2.50 / $10 |
| Reranking | azure.gpt-4o-mini | Azure OpenAI | Small | $0.15 / $0.60 |
| Contextualize | azure.gpt-4o-mini | Azure OpenAI | Small | $0.15 / $0.60 |
| OCR / Document | azure.gpt-4o | Azure OpenAI | Medium | $2.50 / $10 |
| Embeddings | azure.text-embedding-3-small | Azure OpenAI | - | $0.02 / 1M tokens |

All models accessed via GenAI LiteLLM proxy (`BASE_URL` in `.env`).

## Architecture

```text
+-----------------------------------------------------+
|                  PySide6 Desktop App                 |
|  +---------+  +----------+  +--------+  +--------+  |
|  | Project |  |  Board   |  | Detail |  |  Chat  |  |
|  |  Rail   |  |   Grid   |  |  Pane  |  | Dialog |  |
|  +---------+  +----------+  +--------+  +--------+  |
+------|-------------|-------------|------------|------+
       |             |             |            |
+------v-------------v-------------v------------v------+
|                   Core Layer                         |
|  app_config | model_router | anthropic_client        |
|  settings_store | project_store | network_status     |
+------|------------------------------|----------------+
       |                              |
+------v------+       +--------v--------+  +----------+
|  KB Engine  |       |  ADO Integration |  |  Jira    |
|  retrieval  |       |  ado/api         |  |  jira/   |
|  embeddings |       |  ado/boards      |  |  api     |
|  indexer    |       |  ado/work_items  |  |  boards  |
|  bm25       |       +-----------------+  +----------+
|  kb_crypto  |
|  context_   |
|  summary    |
+-------------+
       |
+------v------+
|  GenAI Proxy |  (LiteLLM - multi-provider gateway)
|  /chat/completions  |  /embeddings
+--------------+
```

## Workflow

### Test Case Generation (v3.0)

```
1. User selects work items in Board Grid (ADO or Jira)
2. User clicks "Generate Test Cases" (Implementation/SIT/UAT)
3. Project Context Summary injected (actors, entities, workflows, business rules)
4. model_router.route(GENERATE_TEST_CASES) -> Opus
5. KB retrieval (hybrid BM25 + vector) fetches relevant context
6. LLM generates structured test cases with extended thinking
7. Quality scoring: avg score + flagged low-quality TCs
8. Traceability matrix: coverage % per work item
9. Test data enrichment: pattern-based suggestions per step
10. Output written to Excel + JSON sidecar
11. Board grid refreshes Coverage + Execution Status columns
```

### KB Indexing

```
1. User uploads documents (80+ formats - see Supported Formats below)
2. Text extraction: format-specific extractors, API OCR for scans,
   Whisper API for audio, GPT-4o vision for images
3. Chunking: coarse (~6000 tokens) then dense (~550 tokens, no overlap)
4. Contextualization: GPT-4o-mini summarizes each chunk (situating prefix)
5. Embeddings: API call to azure.text-embedding-3-small (512-dim)
   - Input truncated to 30K chars (safety for 8191 token limit)
6. Storage: vectors.npy + BM25 index + manifest (all encrypted at rest)
7. Retrieval: query -> embed -> cosine sim + BM25 -> LLM rerank -> top-K
```

**Supported Formats:**

- Text/Code: .py .js .ts .jsx .tsx .java .cs .cpp .c .h .hpp .go .rb .php
  .swift .kt .scala .rs .lua .pl .r .m .f90 .vb .dart .zig .nim .ex .erl
  .hs .clj .lisp .ml .fs .groovy .coffee .v .sv
- Config: .yaml .yml .toml .ini .cfg .conf .properties .env
- Docs: .md .txt .rst .adoc .tex .bib .wiki .org .rtf
- Data: .json .jsonl .csv .tsv .xml .html .htm .geojson .graphql .proto
  .thrift .avsc
- Web: .css .scss .sass .less .svg .wasm
- Build: .makefile .cmake .gradle .sbt .cabal .podspec
- Shell: .sh .bash .zsh .fish .bat .ps1 .cmd
- Office: .docx .xlsx .pptx .pdf
- Legacy: .doc .ppt .xls .msg .odt .eml .epub
- Multimedia: images (OCR via GPT-4o vision), audio/video (Whisper API)
- Archives: .zip (extracted, up to 500 files indexed recursively)

**Guards:**

- 50 MB file-size cap (reads first 50MB of oversized files)
- 30K char embedding truncation (8191 token API limit safety)
- Batch processing with gc.collect() between files (4GB RAM safe)

### Hybrid Retrieval

```
Query -> [Embed via API] -> Cosine similarity (dense)
      -> [BM25 tokenize]  -> BM25 scores (sparse)
      -> RRF merge -> LLM rerank (GPT-4o-mini) -> Top-K chunks
```

## Security

| Layer | Mechanism |
|-------|-----------|
| API Key storage | DPAPI-encrypted `.env.enc` (Windows) |
| ADO PAT storage | Keyring (Win Cred Mgr) primary; DPAPI-encrypted file fallback |
| KB data at rest | DPAPI + machine-key XOR fallback |
| File format | 4-byte magic `KBEV` + variant byte + encrypted payload |
| Transport | HTTPS + Zscaler/corporate proxy compatible |
| Secrets in memory | Cleared after use; never logged |
| Build pipeline | Hard-fails if only plaintext `.env` exists (no accidental leak) |

## Network Status

Status bar indicator (NW dot):
- **Green**: API call succeeded within last 30s
- **Yellow**: Idle (no recent API activity)
- **Red**: API unreachable / last call failed

## Configuration

All runtime config in `src/.env` (shipped encrypted as `.env.enc`):

```
MODEL_SMALL=azure.gpt-4o-mini
MODEL_MEDIUM=bedrock.anthropic.claude-sonnet-4-6
MODEL_LARGE=bedrock.anthropic.claude-opus-4-6
MODEL_RERANK=azure.gpt-4o-mini
MODEL_CONTEXTUALIZE=azure.gpt-4o-mini
MODEL_EXTRACT=azure.gpt-4o
MODEL_GENERATE=bedrock.anthropic.claude-opus-4-6
MODEL_CHAT=bedrock.anthropic.claude-sonnet-4-6
MODEL_OCR=azure.gpt-4o
BASE_URL=https://genai-sharedservice-americas.pwcinternal.com
API_KEY=<service-account-key>
EMBED_MODEL=azure.text-embedding-3-small
```

## v3.0 Modules

| Module | File | Purpose |
|--------|------|---------|
| Project Context Summary | kb/context_summary.py | Deep domain understanding on KB upload (actors, entities, workflows, rules) |
| Traceability Matrix | testgen/traceability.py | Maps work items to test cases, coverage % |
| Quality Scorer | testgen/quality_scorer.py | Rule-based TC quality grading (step count, specificity, duplication) |
| Test Data Enrichment | testgen/test_data.py | Pattern-based test data suggestions per step |
| Diff Engine | testgen/diff_engine.py | Compare old vs new generated payloads |
| Execution Store | automation/execution_store.py | Persist E2E run results, history, re-run failed |
| Credential Vault | automation/credential_vault.py | Encrypted per-project credential storage with AI instructions |
| Jira Service | services/jira_service.py | Singleton Jira API wrapper (mirrors ado_service) |
| GenCache | testgen/gen_cache.py | SHA-256 content-addressed two-tier disk+memory cache |

### Multi-Source Architecture (ADO + Jira)

```text
project_source(full_name) -> "ado" | "jira"
    |
    +-> ADO path: ado_service -> ado/api + ado/boards
    |                         -> ado/testcase_creator (upload)
    |
    +-> Jira path: jira_service -> jira/api + jira/boards
                                -> jira/testcase_creator (upload, Xray support)
```

Both backends share the same:
- KB indexing pipeline (project context extraction)
- RLM generation pipeline (source-agnostic once work item text is extracted)
- Quality scoring + traceability
- Test data enrichment
- Excel template rendering
- E2E automation runner (with per-credential AI instructions for login steps)

### E2E Artifact Handling

- **Screenshots**: Saved immediately to disk after each step (`page.screenshot(path=...)`)
  with annotated overlay (step number, status, label). UI notified via `on_screenshot` callback.
- **Video**: Playwright only finalizes .webm on `context.close()`. Workaround: after each step,
  the in-progress video file is copied to `{tc_id}/video/recording_live.webm` so partial
  recordings are always available on disk even if the test crashes mid-run.
- **Scripts**: Generated rerunnable Playwright script saved per test case (uses
  `os.environ["E2E_PASSWORD"]` placeholder - password never written to disk).
- **Real-time status**: `on_tc_done(tc_id, status)` callback emitted after each TC completes.
  E2EDialog updates per-TC color badges (green/red/gray) and per-WI aggregate counts in bulk mode.

### v3.0 UI Changes (CRS Implementation)

- **Regenerate button removed** from action bar (still accessible per-file in artifacts panel)
- **Run Test conditional**: enabled when selected/focused work items have generated test cases in the sidecar JSON (checkbox selection or double-click focus). Opens maximized with `<WI ID> - <Title>` in the window title. Bulk mode (multiple WIs) shows left panel with per-WI aggregate status and per-TC pass/fail/skip badges during execution.
- **Credentials button** accessible from both activity bar (icon strip) and nav panel (bottom row)
- **AI Instructions field** in credential dialog: free-text login hints passed to the E2E runner
- **Chat dialog**: input field full-width on its own row; attach/send/stop below; source logo + prefix-stripped display name in header
- **Clear ADO credentials** button in global settings (removes stored PAT)
- **Dynamic source labels**: Upload button text adapts to "Upload to ADO" or "Upload to Jira"

## Key Design Decisions

1. **API-only processing**: ALL heavy lifting (embeddings, OCR, transcription, reranking, generation) runs via API models. Only local storage remains local (numpy vectors, BM25 index). No local ONNX, no local Whisper, no local RapidOCR.
2. **Multi-provider over single-vendor**: 100x cost reduction on trivial tasks (rerank/contextualize) by using GPT-4o-mini instead of Opus.
3. **API embeddings over local ONNX**: Eliminates 400MB+ model download, consistent quality, no GPU dependency.
4. **GPT-4o vision for OCR**: Scanned PDFs rasterized locally (PyMuPDF), each page sent to GPT-4o vision API for text extraction. Higher accuracy than local RapidOCR, no ONNX dependency.
5. **Whisper API for audio**: Audio/video transcription via `/audio/transcriptions` endpoint. No local faster-whisper model needed.
6. **LLM reranking**: GPT-4o-mini reranks candidates via structured JSON prompt. Replaces local ONNX cross-encoder for consistent API-only architecture.
7. **DPAPI encryption**: Zero-config on Windows; machine-key fallback for edge cases.
8. **Streaming-first data processing**: Polars lazy API; works on 4GB RAM laptops.
9. **LiteLLM proxy**: Single endpoint, unified auth, provider-agnostic model routing.
10. **Event-driven streaming ADO fetch**: Progressive loading via `StreamingAsyncWorker` -- projects, boards, and work items render in the UI as each batch arrives from the API (200-item batches). No waiting for full payload. `sys.intern()` on repeated string fields, `@dataclass(slots=True)` on all ADO models, monotonic sequence counters for stale-result rejection.
11. **Service-Oriented Architecture (v2.4.0)**: Domain services (`AdoService`, `KbService`, `LlmService`) encapsulate all business logic behind clean async APIs. Services are singletons accessed via import; no DI container (YAGNI). RAM-first `CacheService` with LRU eviction and TTL provides sub-millisecond repeat access. Application-wide `EventBus` (pub/sub) decouples services from UI without Qt signal coupling at the domain layer.
12. **RAM-first caching with TTL**: All ADO data (projects, boards, work items) cached in-memory with configurable TTL (projects: 10min, boards: 5min, rows: 2min). Repeat navigation is instant from RAM. Cache invalidation via event bus on PAT/org change.
13. **Enterprise UI/UX**: Apple HIG + Material Design 3 motion system. Purposeful animations (150ms show/hide, 250ms page transitions). Skeleton shimmer loaders during async fetch. Responsive layout adapts to narrow/medium/wide windows.

## Service Architecture (v2.4.0)

```
+------------------------------------------------------------------+
|                     UI Layer (PySide6)                            |
|  MainWindow | BoardGrid | GenerateDialog | ChatDialog            |
+----------|------------|--------------|-------------|-------------+
           |            |              |             |
+----------v------------v--------------v-------------v-------------+
|                     Service Layer (services/)                     |
|  +------------+  +----------+  +-----------+  +-------------+   |
|  | AdoService |  | KbService|  | LlmService|  | CacheService|   |
|  +-----+------+  +-----+----+  +-----+-----+  +------+------+   |
|        |               |              |                |          |
|  +-----v---------------v--------------v----------------v------+  |
|  |              EventBus (pub/sub broker)                      |  |
|  +------------------------------------------------------------+  |
+------------------------------------------------------------------+
           |               |              |
+----------v--+  +---------v---+  +-------v--------+
| ado/ module |  | kb/ module  |  | core/ (LLM,    |
| (httpx API) |  | (indexer,   |  |  model_router, |
|             |  |  retrieval) |  |  client)       |
+-------------+  +-------------+  +----------------+
           |               |              |
+----------v---------------v--------------v--------+
|              GenAI LiteLLM Proxy                  |
|  /chat/completions | /embeddings | /audio        |
+--------------------------------------------------+
```

### Event Topics

| Topic | Emitter | Subscribers |
|-------|---------|-------------|
| `ado.projects.loaded` | AdoService | MainWindow |
| `ado.projects.batch` | AdoService | MainWindow (streaming) |
| `ado.boards.loaded` | AdoService | MainWindow |
| `ado.board_view.rows_batch` | AdoService | BoardGrid |
| `cache.invalidated` | CacheService | (diagnostic) |
| `kb.index.progress` | KbService | StatusBar |
| `kb.index.complete` | KbService | MainWindow |
| `llm.usage` | LlmService | StatusBar (cost) |
| `network.status_changed` | NetworkStatus | StatusDot |

### Cache Namespaces

| Namespace | Max Size | TTL | Content |
|-----------|----------|-----|---------|
| `ado.projects` | 32 | 10 min | Project name lists by org |
| `ado.boards` | 128 | 5 min | Board lists by project |
| `ado.rows` | 64 | 2 min | Work item rows by board |
| `kb.retrieval` | 256 | 1 min | Recent query results |
