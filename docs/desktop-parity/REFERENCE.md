> **Historical desktop parity snapshot:** retained for comparison only; do not use
> it as current security, secret-management, or installation guidance.

# Testing Toolkit - Plain English Reference

A no-nonsense explanation of what this app is, how it works, and what
technology powers it. Written so anyone can understand it.

---

## What Is This App?

Testing Toolkit is a desktop program for QA testers who use Azure DevOps
(ADO) or Atlassian Jira. It sits on your Windows/Mac/Linux machine and
does these things:

1. **Writes test cases for you** using AI (with quality scoring and traceability)
2. **Packages work items into PDFs** for sharing or archiving
3. **Runs E2E tests** via browser automation with real-time per-TC status and bulk WI mode
4. **Provides an AI chatbot** for ad-hoc questions and generation
5. **Manages test credentials** securely per project/environment

It connects to your company's Azure DevOps boards or Atlassian Jira
projects, reads the user stories and requirements, then uses multiple
AI models to generate professional test cases -- complete with steps,
expected results, test data suggestions, quality scores, and
traceability back to the original requirements.

---

## Who Is It For?

QA engineers, SDETs, and test leads who:

- Work in Azure DevOps or Atlassian Jira
- Spend hours writing test cases manually
- Want AI assistance without leaving their workflow

---

## How It Works (The 30-Second Version)

```text
You pick work items from your ADO board (or Jira project)
    -> The app reads the requirements
    -> AI generates test cases (with quality scoring)
    -> Traceability matrix shows coverage gaps
    -> Test data suggestions auto-enriched
    -> You review in Excel
    -> One click uploads them back to ADO / Jira
    -> Run E2E tests in your browser
    -> Track execution history and re-run failures
```

That's it. The complexity is hidden behind one window.

---

## The Main Screen

The app looks like VS Code -- a dark-themed window with panels:

```
+---+----------+----------------------------------+-----------+
| I |          |                                  |           |
| C | Projects |  Your Board (cards/list view)    |  Details  |
| O |          |                                  |  (HTML)   |
| N | Boards   |  [Generate] [Package] [Defects]  |           |
| S |          |                                  |           |
+---+----------+----------------------------------+-----------+
| Status: CPU 12% | RAM 245 MB | Network: Green | Disk: 89 MB |
+-------------------------------------------------------------|
```

- **Left rail**: Pick your project and board
- **Center**: See all work items (kanban cards or tree list)
- **Right**: Full details of any item you click
- **Bottom bar**: Shows app health (CPU, memory, network status)

All panels can be hidden/shown. Preferences are remembered between sessions.

---

## Feature 1: Test Case Generation

### What it does

You select one or more user stories from the board. Click "Generate Test
Cases." The AI reads the story, its acceptance criteria, any attached
documents in your knowledge base, and produces structured test cases.

### How the AI thinks (the RLM pipeline)

The generation uses a 5-step process called the Recursive Language Model:

| Step | What happens | Plain English |
|------|-------------|---------------|
| 1. Navigate | AI scans your knowledge base for relevant docs | "Which documents talk about this feature?" |
| 2. Map | AI extracts key facts from those docs | "What rules and constraints apply?" |
| 3. Decompose | AI breaks requirements into atomic checks | "What exactly needs to be tested?" |
| 4. Generate | AI writes full test cases (with deep reasoning) | "Write the tests, think hard about edge cases" |
| 5. Verify | AI checks coverage and fills gaps | "Did I miss anything? Fix it." |

The generate step uses "Extended Thinking" -- the AI literally reasons
through the problem for up to 10,000 tokens before writing anything.
This is like a human pausing to plan before writing.

### Output

- An Excel file with all test cases for review
- You can edit, mark some as "Skip", add feedback
- Then regenerate (up to 10 times with different instructions)
- Or upload directly to ADO as Test Case work items

### Template support

If your client has a specific Excel test-script format, upload their
template once. The AI analyzes it (column layout, formatting, sheet
structure) and all future generation fills THAT template exactly.

---

## Feature 2: Knowledge Base (KB)

### What it is

A per-project document library that gives the AI context. Upload your
requirements documents, design specs, business rules -- anything the AI
should know when writing test cases.

### What it accepts

80+ file formats:

- **Documents**: Word, Excel, PowerPoint, PDF, RTF
- **Legacy**: Old .doc, .ppt, .xls, .msg email files
- **Code**: Python, JavaScript, Java, C#, Go -- 40+ languages
- **Data**: JSON, CSV, XML, HTML
- **Config**: YAML, TOML, .env, properties files
- **Scanned PDFs**: Automatically OCR'd (text extracted from images)
- **Images**: Text extracted via AI vision
- **Audio/Video**: Transcribed to text automatically
- **Zip files**: Extracted and indexed (up to 500 files)

### How search works

When the AI needs information from your KB, it uses a hybrid search:

1. **Keyword search (BM25)**: Finds documents with matching words
   (great for error codes, field names, exact terms)
2. **Meaning search (vectors)**: Finds documents with similar meaning
   even if different words are used
3. **Merge**: Combines both results using a formula (RRF)
4. **AI rerank**: A small AI model re-scores the top results for relevance

This finds the right context 49-67% more often than keyword search alone.

### How documents are processed

```
Your file
  -> Text extracted (format-specific: Word parser, PDF reader, etc.)
  -> Split into chunks (~550 tokens each, roughly half a page)
  -> Each chunk gets a short AI-written summary prepended
  -> Each chunk converted to a number vector (512 numbers)
  -> Stored encrypted on disk
```

### Security

All KB data is encrypted at rest using Windows DPAPI. If someone copies
your files, they cannot read the KB content without your Windows login.

---

## Feature 3: PDF Packaging

### What it does

Packages selected work items into professional PDFs:

- **Per-item PDF**: Cover page + description + attachments (all converted
  to PDF -- Word, Excel, images, existing PDFs)
- **Combined PDF**: All selected items merged into one document
- **KB bundle**: Split into AI-ready chunks for uploading to other systems

---

## Feature 4: AI Chatbot

### What it does

A general-purpose AI chat window for ad-hoc tasks:

- Ask questions about your project
- Generate content (emails, summaries, analysis)
- Attach files and images for context
- Multiple conversations saved and searchable
- Streaming responses (text appears word-by-word)
- Save code/artifacts to files

---

## Feature 5: E2E Test Automation

### What it does

Runs your generated test cases in a real browser (your actual browser
with SSO already logged in). The app attaches to Chrome/Edge via CDP
(Chrome DevTools Protocol) and executes steps automatically.

### How to use it

1. Select work items that have generated test cases (checkboxes or
   double-click a single story)
2. Click "Run Test" (only enabled when selected WIs have test cases)
3. The dialog opens maximized showing the WI title and test case list
4. Pick an environment (credentials stored securely per project)
5. Select which test cases to run (or "Select All")
6. Click "Run E2E Tests"

### Bulk mode (multiple work items)

When you select multiple work items, the dialog shows a left panel:

```
+-------------------+----------------------------------+
| Work Items        | Test Cases                       |
|                   |                                  |
| [All Work Items]  | [PASS] Login validation          |
| 12345 - Login...  | [FAIL] Password reset flow       |
|   (2/3 pass)      | [RUNNING] Session timeout        |
| 67890 - Cart...   |                                  |
|   (pending)       | Progress: [====     ] 40%        |
|                   | Log output...                    |
+-------------------+----------------------------------+
| [Run E2E Tests]  [Re-run Failed]  [Stop]  [Report]  |
+------------------------------------------------------+
```

- Click a WI in the left list to filter test cases to that WI only
- Each WI shows its aggregate status (pass count, fail count)
- Colors: green = all passed, red = failures, yellow = in progress

### After the run

- **Report**: Excel report generated automatically (Open Report button)
- **Re-run Failed**: One click to re-run only the failed test cases
- **Execution history**: Results saved per project; last run summary always
  shown at the top of the dialog
- **Artifacts**: Screenshots, video recordings, and rerunnable scripts
  saved per test case

---

## The AI Models

The app uses MULTIPLE AI models, each chosen for what it does best:

| Task | Model | Why this one |
|------|-------|-------------|
| Writing test cases | Claude Opus (largest) | Best reasoning, deepest thinking |
| Verifying coverage | Claude Opus | Correctness matters most here |
| Breaking down requirements | Claude Opus | Quality of decomposition drives everything |
| Chat responses | Claude Sonnet (medium) | Fast enough for conversation, still smart |
| Navigating documents | Claude Sonnet | Speed + quality balance |
| Extracting data from docs | GPT-4o | Strong at structured output (JSON) |
| Parsing defect documents | GPT-4o | Reliable field extraction |
| Reading scanned documents | GPT-4o | Has vision (can see images) |
| Ranking search results | GPT-4o-mini (smallest) | Trivial task, 100x cheaper |
| Summarizing chunks | GPT-4o-mini | Simple task, no need for expensive model |
| Creating text vectors | text-embedding-3-small | Purpose-built for search |

### Why multiple models?

Cost and speed. Using the biggest AI for every task would be like hiring
a brain surgeon to apply a band-aid. The cheap model handles simple jobs
(ranking, summarizing) at 1/500th the cost. The expensive model only
activates for the hard thinking (writing test cases, verifying coverage).

### How they connect

All models are accessed through a single gateway called "LiteLLM proxy"
hosted at the company's GenAI service. The app sends requests there, and
the proxy routes them to the correct provider (Anthropic for Claude,
Azure for GPT). One API key, one endpoint, multiple brains.

```
App -> GenAI LiteLLM Proxy -> Anthropic (Claude models)
                           -> Azure OpenAI (GPT models)
                           -> Azure OpenAI (Embeddings)
```

---

## The Tech Stack

### Programming Language

- **Python 3.12** -- the entire app is Python

### Desktop UI Framework

- **PySide6** (Qt 6) -- the same framework used by VLC, OBS, and
  Autodesk Maya. Gives native-looking windows, buttons, and layouts.
- Dark theme following Material Design 3 guidelines

### Key Libraries

| Library | What it does |
|---------|-------------|
| PySide6 | Desktop window, buttons, panels, dialogs |
| httpx | Makes HTTP calls to ADO and AI APIs |
| openpyxl | Reads and writes Excel files |
| pypdf | Reads PDF text |
| reportlab | Creates new PDF files |
| Pillow | Handles images (resize, convert, embed) |
| python-docx | Reads Word documents |
| python-pptx | Reads PowerPoint files |
| numpy | Math for search vectors and BM25 scoring |
| keyring | Stores passwords in Windows Credential Manager |
| certifi + truststore | Handles corporate proxy certificates (Zscaler) |

### External Services (consumed, not hosted)

| Service | What the app uses it for |
|---------|------------------------|
| Azure DevOps REST API | Read boards, work items, create test cases, upload bugs |
| GenAI LiteLLM Proxy | All AI model calls (generation, chat, OCR, embeddings) |
| MCP Server (bundled) | Specialized ADO operations via Model Context Protocol |

### No Database

The app does NOT use a database. All data lives in:

- JSON files (settings, preferences, KB indexes)
- NumPy arrays (search vectors)
- Excel files (test case output, defect review)
- PDF files (packaged output)
- Windows Credential Manager (PAT storage)

---

## How It Installs and Runs

### Zero-Install Mode (the common case)

The app ships as a self-contained folder. No Python install needed.
No internet needed on the target machine. Double-click `install.cmd`
and it:

1. Uses the bundled portable Python (27 MB, included)
2. Installs all packages from pre-downloaded wheel files (offline)
3. Builds a standalone .exe via PyInstaller
4. Launches the app

Takes about 2-3 minutes on first run. No admin rights required.
No registry changes. Nothing installed system-wide.

### What's in the folder

```
Testing Toolkit/
    install.cmd          <- Double-click this (Windows)
    install.sh           <- Run this (Mac/Linux)
    python-embed/        <- Portable Python (no system install needed)
    src/                 <- All source code + assets
        wheelhouse/      <- Pre-downloaded packages (offline install)
        models/          <- AI model files (for offline search)
        assets/          <- Icons and images
```

### Supported Platforms

| Platform | Architecture |
|----------|-------------|
| Windows 10/11 | Intel/AMD 64-bit |
| Windows 11 | ARM (Snapdragon) |
| macOS | Intel |
| macOS | Apple Silicon (M1-M4) |
| Linux | 64-bit |

---

## Security

| What | How it's protected |
|------|-------------------|
| AI API key | Encrypted with Windows DPAPI, never stored as plain text |
| ADO Personal Access Token | Stored in Windows Credential Manager |
| Knowledge Base content | Encrypted at rest (DPAPI + machine key fallback) |
| Network traffic | HTTPS everywhere, compatible with Zscaler proxy |
| LLM usage | Guardrails block off-topic prompts before they reach the AI |

### What DPAPI means

Windows Data Protection API. It encrypts data tied to YOUR Windows login.
Even if someone copies the encrypted file to another machine or another
user account, they cannot decrypt it. No passwords to remember -- your
Windows login IS the key.

---

## Performance and Resource Usage

### Memory

The app is designed to run on laptops with as little as 4 GB RAM:

- Streaming data processing (never loads everything at once)
- Memory-aware batch sizes (smaller batches on low-RAM machines)
- Explicit memory cleanup after heavy operations
- Typical running usage: 200-400 MB

### Speed

| Operation | Typical time |
|-----------|-------------|
| App startup | ~2 seconds |
| Load a board (500 items) | ~3-5 seconds (streams progressively) |
| Search knowledge base | ~2-3 seconds |
| Generate test cases (5 stories) | ~30-90 seconds (depends on complexity) |
| OCR a scanned PDF (10 pages) | ~10-20 seconds |

### Hardware detection

On startup, the app detects your hardware and optimizes:

- **CPU cores**: Sets thread pool sizes
- **RAM**: Adjusts batch sizes and cache limits
- **GPU** (if available): Enables accelerated processing
- All detection is safe -- if something fails to detect, conservative
  defaults are used. No crashes from hardware detection.

---

## Network Requirements

The app needs network access to TWO services:

1. **Azure DevOps** (`dev.azure.com`) -- to read/write work items
2. **GenAI Proxy** (company internal) -- for all AI model calls

If the network is down:

- You can still browse previously-cached boards (from memory)
- KB search falls back to keyword-only (no AI reranking)
- Generation and chat obviously need the AI service
- The status bar shows a red dot when offline

---

## How the Event System Works

The app uses a publish/subscribe pattern internally. Think of it like
a message board where different parts of the app post updates:

- "Hey, projects just loaded" -> the left panel updates its list
- "Hey, work items arrived (batch 3 of 7)" -> the board shows progress
- "Hey, the network went down" -> the status dot turns red
- "Hey, KB indexing is 60% done" -> the progress bar updates

This keeps the UI responsive. Nothing blocks waiting for slow operations.

---

## How ADO Integration Works

### Authentication

You provide a Personal Access Token (PAT) once during first-run setup.
It's stored in Windows Credential Manager (not a file). The app uses
this token for all ADO API calls.

### What it can do in ADO

- List all projects you have access to
- List boards within a project
- Read all work items on a board (streaming, 200 at a time)
- Read work item details (description, attachments, comments)
- Create Test Case work items (as children of stories)
- Create Bug work items (with full fields populated)
- Download attachments from work items

### MCP (Model Context Protocol)

Some ADO operations use a bundled "MCP server" -- a small Node.js
process that speaks a specialized protocol designed for AI tool use.
This is invisible to the user; it's an implementation detail for
how the chatbot can perform ADO operations when you ask it to.

---

## The Build System

### For developers (building from source)

One command: `python build.py`

This does everything:
1. Cleans previous build artifacts
2. Installs all dependencies
3. Runs environment checks
4. Encrypts the API key file
5. Bundles everything into a standalone folder with PyInstaller

### For deployment (air-gapped machines)

Two preparation steps on a connected machine:
1. `python make_portable.py` -- downloads portable Python
2. `python make_wheelhouse.py` -- downloads all packages as files

Then copy the whole folder to the target machine and run `install.cmd`.

---

## Glossary

| Term | Meaning |
|------|---------|
| ADO | Azure DevOps -- Microsoft's project management and source control platform |
| PAT | Personal Access Token -- like a password for API access to ADO |
| KB | Knowledge Base -- your uploaded reference documents |
| RLM | Recursive Language Model -- the multi-step test generation pipeline |
| LLM | Large Language Model -- the AI (Claude, GPT, etc.) |
| BM25 | A keyword search algorithm (like Google but simpler) |
| Vector / Embedding | A list of numbers representing meaning of text (for semantic search) |
| RRF | Reciprocal Rank Fusion -- formula for combining two ranked lists |
| DPAPI | Windows Data Protection API -- encryption tied to your Windows login |
| LiteLLM | A proxy that lets one app talk to multiple AI providers through one door |
| MCP | Model Context Protocol -- a way for AI to call external tools |
| PyInstaller | Packages Python apps into standalone .exe programs |
| PySide6 / Qt | The UI framework (makes windows, buttons, etc.) |
| Zscaler | Corporate web proxy that inspects HTTPS traffic |
| Extended Thinking | AI feature where the model reasons internally before answering |
| Streaming | Data arriving piece by piece (not waiting for all of it) |
| Chunking | Splitting large documents into smaller pieces for processing |
| Reranking | Using AI to re-sort search results by relevance |
| OCR | Optical Character Recognition -- reading text from images |
| Whisper | OpenAI's speech-to-text model (for audio/video transcription) |

---

## Version History (Key Milestones)

| Version | What changed |
|---------|-------------|
| v2.0 | Initial release: test generation + PDF packaging |
| v2.1 | Added AI chatbot, defect review Excel generation |
| v2.2 | VS Code-style navigation, streaming ADO fetch, display polish |
| v2.3 | Service-oriented architecture, event bus, caching layer |
| v2.4 | Multi-provider AI (Claude + GPT), KB encryption, network status |
| v3.0 | Jira parity, deep context understanding, traceability matrix, quality scoring, test data enrichment, E2E execution tracking, bulk regeneration, credential management |
| v3.1 | E2E bulk mode (per-WI/per-TC status), Run Test enables on WI selection, maximize dialog |

---

## Cost Model

The AI usage costs money per token (like per-word billing). The app
optimizes this aggressively:

| What | Cost per use (approximate) |
|------|--------------------------|
| Generate test cases (5 stories) | $0.50 - $2.00 |
| Chat message | $0.01 - $0.05 |
| Index a 50-page document | $0.10 - $0.30 |
| OCR a 10-page scanned PDF | $0.05 - $0.15 |
| Search the knowledge base | $0.005 |

The multi-model approach saves roughly 100x on trivial operations compared
to using the most expensive model for everything.

---

## What It Does NOT Do

- Does NOT host a web server (it's a desktop app)
- Does NOT store data in the cloud (everything is local)
- Does NOT require admin/root privileges
- Does NOT modify your ADO boards (only reads them; writes only when
  you explicitly click Upload)
- Does NOT send your data anywhere except the configured AI proxy and ADO
- Does NOT require internet on the target machine (for install/build)
- Does NOT work without eventual network access to ADO and the AI service
  (for actual generation/upload operations)

---

## Architecture Summary (One Picture)

```
+------------------+
|  You (the user)  |
+--------+---------+
         |
+--------v---------+
|  Desktop App     |  PySide6 dark UI
|  (Windows/Mac)   |  Runs locally, no browser
+--------+---------+
         |
    +----+----+
    |         |
+---v---+ +---v---+
|  ADO  | |  AI   |  Two external connections
|  API  | | Proxy |  Everything else is local
+-------+ +---+---+
              |
     +--------+--------+
     |        |        |
  Claude    GPT-4o   Embeddings
  (smart)   (vision)  (search)
```

That's the whole system. A local desktop app that talks to your ADO
instance and an AI gateway. Nothing else.
