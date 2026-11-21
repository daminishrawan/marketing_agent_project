# RECOVERY_PLAN.md — MarketMind AI Ingestion & Retrieval

**Owner:** Damini Shrawan (knowledge / memory / tools)
**Status:** Approved for execution. Execute one part at a time; each part ends with a gstack gate.
**Scope of this plan:** Ingestion, hybrid retrieval, benchmark, `knowledge-kb` MCP server, lead records. Campaign-checks, HMAC publisher, LangGraph, and FastAPI are out of this plan and belong to the wider team.

---

## 0. Working Assumptions (defaults until you say otherwise)

1. `comp_cloudvane_01` maps to the MOSETA brand pack (Brand A). Folder names stay as the team already uses them. Brand B = `comp_pulsepeak_02`, Brand C = `comp_terraroast_03`.
2. No OpenRouter key yet. Embeddings run behind an interface with a deterministic local backend for tests and a `baai/bge-m3` OpenRouter backend activated by `OPENROUTER_API_KEY`. Document and query use the same backend.
3. The 30+ benchmark queries are drafted from the source text and marked `"label_status": "self-drafted"`. They are not evaluation-grade until Dr. Ashutosh Verma replaces or confirms them.
4. Python 3.12 venv at `.venv/`. The current 3.13 environment is not used for the build.

---

## 1. Architecture (target)

```
data_input/<company_id>/*.{html,pdf,md,txt}
        │
        ▼
src/mm/ingest.py        sieve (hidden HTML, Prompt Guard stub) → parse → chunk (500/50)
        │  list[Passage]  (passage_id deterministic)
        ▼
src/mm/index.py         Chroma (dense, filtered by company_id)  +  per-tenant BM25
        │
        ▼
src/mm/search.py        3 query variants → dense + lexical (top 50 each) → RRF(k=60)
                        → cross-encoder rerank on top 20 → top_k
        │
        ├──► src/mm/bench.py     Recall@5, baseline (1 query) vs multi-query (3)
        └──► src/mm/mcp_kb.py    FastMCP "knowledge-kb": search_knowledge, get_passage, get_brand_profile

records.db  ◄── src/mm/records.py   leads only; never imported by ingest/index
```

**Hard rules enforced in code and tested:**
- Leads never enter the vector store.
- Tenant isolation: separate BM25 index per `company_id` plus a Chroma `where` filter. A cross-tenant query returns zero passages.
- `approved_flag` defaults to `False` for brand profiles. `get_brand_profile` returns a pending-approval error until `True`.
- Outside text is stored raw. `<source_data>` delimiters are added only in a prompt-formatting helper, never written to storage or embeddings.

**Passage ID format:** `{company_id}:{source_sha12}:{chunk_index}`
- `source_sha12` = first 12 hex chars of SHA-256 over the normalised source text (not the chunk text, not the date).
- Same file → same IDs on every run. Edited file → new `source_sha12`, old IDs retired explicitly.

**Shared contract (`src/mm/models.py`):** `Passage`, `ChunkMetadata`, `SearchHit`, `SearchResult`, `ErrorPayload`, `BrandProfile`. Changes require a version bump in the module docstring and an update to every test that imports it.

---

## 2. Stabilization: Known Defects to Fix at Root Cause

The pasted engineering guide and earlier scripts contain these defects. None is patched; each is removed by the new design.

| # | Defect | Root cause | Fix location |
|---|---|---|---|
| D1 | Dense query uses Chroma default embedder, docs use bge-m3 | Two embedding paths | `embed.py` single interface, index and query both call it |
| D2 | `results["ids"]` parsed as flat list | Chroma returns nested lists | `index.py` unwraps `[0]` in one place, tested |
| D3 | RRF sorted by `lambda x: x` (sorts by id) | Wrong key | `search.py` sorts by fused score; unit test with known ranks |
| D4 | BM25 tokenises on whitespace only | No normalisation | `index.py` tokenizer: lowercase, split on non-alphanumerics, keep model codes like `moto`, `ods`, `1500w` |
| D5 | Passage IDs depend on date and chunk index; re-ingest drifts | ID includes mutable metadata | Deterministic ID scheme above |
| D6 | `approved_flag = True` hard-coded | No review state | `BrandProfile.approved_flag` default `False` |
| D7 | BM25 global, not per tenant | Shared dict | Per-company index; test isolates |
| D8 | Deprecated `langchain.text_splitter` import | Version drift | Use our own recursive splitter (no LangChain dependency for this slice) |
| D9 | Env drift: Python 3.13 + opentelemetry resolver conflict | Ad hoc installs | Clean 3.12 venv, pinned `requirements.txt` |
| D10 | Domain and folder mismatch (`cloudvane.io` in config vs MOSETA source; `knowledge_base.json` says TVS/ebike while text is MOSETA) | Earlier data was not reconciled | Reconcile in Part 1B; flag `knowledge_base.json` as inconsistent and do not use its `company_profile` |
| D11 | Brand B and C never written | Plans narrated, files not created | Part 1B produces them, verified by `BrandProfile` validation |
| D12 | Cross-encoder loaded at import time | Slow tests, hidden dependency | Lazy-load inside `search.py`; tests inject a fake reranker |
| D13 | Empty or unreadable PDF not handled | Missing error path | `UNREADABLE_FILE_ERROR` returned as `ErrorPayload`, not raised |

---

## 3. Execution Parts

Each part must pass its **Done** command before the next starts. Each ends with the gstack gate listed in Section 4.

### Part 1A — Environment and contracts
- Create `.venv` (Python 3.12). Write `requirements.txt` with pinned versions: `fastmcp`, `chromadb`, `rank-bm25`, `sentence-transformers`, `pydantic>=2`, `pypdf`, `beautifulsoup4`, `requests`, `pytest`.
- Write `src/mm/__init__.py`, `src/mm/models.py`, `pyproject.toml` (or `pytest.ini`) so tests import `src.mm`.
- Write `config.yaml` (chunk 500/50, rrf_k 60, candidate_pool 50, rerank_top_k 5, multi_query_count 3, allowed_domains reconciled per Part 1B).
- **Done:** `pytest -q tests/test_models.py` passes (model validation, default `approved_flag=False`, passage ID format).

### Part 1B — Brand packs (content)
- Fix `brand_packs/brandA_moseta/brandA_profile.json`: add `approved_flag: false`, `brand_id`, `company_id: comp_cloudvane_01`, and source file reference.
- Write Brand B (PulsePeak, `comp_pulsepeak_02`) and Brand C (TerraRoast, `comp_terraroast_03`) as source `.txt` in `data_input/` and profile JSON in `brand_packs/`. Each has: catalogue with prices, tone, claims restrictions, active offers, synthetic lead examples (not ingested into Chroma).
- Reconcile `config.yaml` `allowed_domains` to the synthetic domains actually used.
- Update `source_inventory.md`: Brand B and C rows, licence, date, version; mark `knowledge_base.json` as inconsistent.
- **Done:** `python -m src.mm.validate_packs` (or a pytest) validates all three profiles against `BrandProfile`; no lead PII in source text.

### Part 1C — Sieve and chunker
- `src/mm/ingest.py`: HTML hidden-element stripping (`display:none`, `visibility:hidden`, `font-size:0`, script/style/noscript/meta), PDF via pypdf, Markdown/TXT read as text. Recursive splitter (500 chars, 50 overlap, separators `\n\n`, `\n`, `. `, ` `, ``).
- Prompt Guard: a stub interface `score_injection(text) -> float` with a keyword heuristic backend; threshold 0.85 quarantines the file. Clearly labelled as a stub in code and docs.
- Returns `list[Passage]` with deterministic IDs.
- **Done:** `pytest -q tests/test_ingest.py` passes: hidden text absent from output; running twice gives identical IDs; empty PDF returns `UNREADABLE_FILE_ERROR`; planted injection quarantined; clean fixture false-block rate 0 on the three packs.

### Part 1D — Index and embeddings
- `src/mm/embed.py`: `Embedder` protocol. `LocalHashEmbedder` (deterministic, used in tests and offline) and `OpenRouterEmbedder` (`baai/bge-m3`, batched, used when key is set).
- `src/mm/index.py`: Chroma persistent collection `marketmind_kb` at `storage/chroma_db`; upsert passages with metadata; per-company BM25 with the D4 tokenizer; `search_dense(company_id, query, n)` and `search_lexical(company_id, query, n)` returning ranked `(passage_id, score)` lists. Chroma nested-list unwrapping done once here (D2).
- Lead text is never accepted by this module (type-level: it only accepts `Passage`).
- **Done:** `pytest -q tests/test_index.py` passes: ingest packs A/B/C, query A returns zero B/C passage IDs, dense and lexical lists are non-empty for an in-brand term, re-index is idempotent.

### Part 1E — Hybrid search
- `src/mm/search.py`: `expand_queries(q) -> [q, q+" pricing terms and offers", q+" restrictions and guidelines"]` (deterministic). Dense and lexical top 50 per variant. RRF k=60 summed over all lists. Top 20 fused → cross-encoder rerank (lazy-loaded; tests inject fake). Return `SearchResult(passages, total_found, error)`.
- `use_multi_query` switch for the benchmark.
- **Done:** `pytest -q tests/test_search.py` passes: RRF hand-computed example matches; fused order is by score (D3 regression test); empty company returns `total_found=0`; no fallback to another tenant.

### Part 1F — Benchmark (Recall@5)
- `evaluation/golden_queries.jsonl`: at least 30 queries per brand set, each with `company_id`, `query`, `gold_passage_ids` (resolved by matching the source sentence to passage IDs after ingest), `label_status`.
- `src/mm/bench.py`: runs baseline (`use_multi_query=False`) and post-change (`True`), prints a markdown table and the delta, writes `storage/reports/recall_at_5.md`.
- **Done:** `python -m src.mm.bench` prints both numbers and writes the report. Target 0.85 is reported honestly whether met or not; failures listed.

### Part 1G — `knowledge-kb` MCP server
- `src/mm/mcp_kb.py`: FastMCP server `knowledge-kb`. Tools: `search_knowledge(company_id, query, top_k 1–20)`, `get_passage(company_id, passage_id)` (rejects IDs not prefixed with `company_id:`), `get_brand_profile(company_id)` (returns pending-approval error when `approved_flag` is false). All tools typed with Pydantic, return `ErrorPayload` on failure, marked read-only.
- **Done:** `pytest -q tests/test_mcp_kb.py` passes (one test per tool, including cross-tenant `get_passage` rejection and unapproved profile). `fastmcp` in-process client call succeeds.

### Part 1H — Lead records (isolated)
- `src/mm/records.py`: SQLite `storage/records.db` with `customer_leads` table per the spec DDL (consent check constraint, qualification 0–100). `create_lead` requires `consent_flag=True`; stores masked contact only.
- **Done:** `pytest -q tests/test_records.py` passes; a grep-style test confirms `records.py` is not imported by `ingest.py` or `index.py`; a lead text search in Chroma returns nothing.

---

## 4. gstack Gate Map

Load the gstack skill before each gate. Gate names below are the intended roles; confirm exact slash names when the skill loads.

| When | Role / skill | Input artifact | Output artifact |
|---|---|---|---|
| Now (this plan) | CEO / product scope | this file + project_details.md | approved scope (Section 0) |
| Before 1A | Eng Manager (`plan-eng-review`) | Section 1 contracts | locked `models.py` |
| After each part | Paranoid reviewer (`review`) | diff of the part | findings list, fixed before next part |
| After each part | QA (`qa`) | part's Done command | test log in `storage/reports/part_<X>.log` |
| After 1C | Security (`cso`) | `ingest.py`, lead path | checklist: no PII in vectors; delimiters not stored |
| After 1F | Eng Manager | `recall_at_5.md` | go / no-go on multi-query claim |
| After 1H, all parts | Release (`ship`) | full test log | only if you ask |

Rule: a part does not start until the previous part's gate artifact exists.

---

## 5. Open Items (blocking only where noted)

- **Blocking for 1F evaluation-grade numbers:** Dr. Ashutosh Verma's labelled queries. Until then, results are labelled self-drafted.
- **Not blocking:** OpenRouter key. Local embedder is used otherwise; OpenRouter backend is wired but untested without a key.
- **Not blocking:** Prompt Guard is a heuristic stub. A real classifier is a later swap behind the same interface.
- **Decision needed later:** whether non-English brand files (Hindi/Marathi) enter the golden set. Current plan is English-only.

---

## 6. Current State at Time of Writing

- Part 1A done: `.venv` (Python 3.12.16), `requirements.txt` and `requirements.lock.txt`, `src/mm/models.py`, `config.yaml`, `tests/test_models.py` (11 passing).
- Part 1B done: Brand A profile fixed (D6), Brand B (PulsePeak) and Brand C (TerraRoast) source and profiles, synthetic leads kept outside `data_input/`, `allowed_domains` reconciled, `tests/test_brand_packs.py` (14 passing).
- Open: Brand A licence question (see `source_inventory.md`); empty stray directories `brand_packs/brandB_lumaskin` and `brand_packs/brandC_flowstack` to delete.
- Part 1C done: `src/mm/ingest.py` (hidden-HTML sieve, PDF/HTML/MD/TXT parse, Markdown comment stripping, heuristic Prompt Guard stub at 0.85, recursive splitter 500/50, deterministic passage IDs, `ErrorPayload` on unreadable or quarantined files), `tests/test_ingest.py`. Full suite: 40 passing. Chunk counts on current packs: MOSETA 27, PulsePeak 9, TerraRoast 9; none quarantined.
- Part 1D done: `src/mm/embed.py` (Embedder protocol, offline `LocalHashEmbedder`, `OpenRouterEmbedder` for `baai/bge-m3` untested without a key, `make_embedder()`), `src/mm/index.py` (Chroma as source of truth, per-company BM25 rebuilt on open and write, source-scoped replacement that retires stale IDs, embedder-mismatch guard, nested-list unwrap D2, tokenizer D4), `tests/test_index.py`. Full suite: 54 passing.
- Part 1E done: `src/mm/search.py` (deterministic 3-variant expansion with a `use_multi_query` switch, RRF k=60 with score-then-id ordering, cross-encoder rerank over the top 20 with a lazily loaded `CrossEncoderReranker`, `INVALID_TOP_K` structured error, empty company returns `total_found=0`), `tests/test_search.py`. The real cross-encoder model has not been loaded in tests; tests inject a fake reranker.
- Part 1F done (with caveats): `evaluation/golden_queries.jsonl` (37 queries, 12/13/12 across brands, all `self-drafted`, gold labels are phrases resolved to passage IDs at run time, so they survive re-ingestion), `src/mm/bench.py`, `tests/test_bench.py`. Report written to `storage/reports/recall_at_5.md`. Full suite: 73 passing.
  - Result: baseline Recall@5 0.973, multi-query 0.973, delta +0.000. Target 0.85 met on this set. Source-ID validity 370/370. One miss: C12 ("how do I know the coffee is fresh").
  - **Not evidence yet.** (a) Embedder is the offline hash stub, so dense retrieval is lexical; the real `baai/bge-m3` run is still pending a key. (b) I wrote the queries after reading the source and reused its wording, so they are easy. (c) Multi-query showed no gain. Re-run after the key is set, and have Dr. Ashutosh Verma write paraphrase queries without seeing the source before this number goes into the submission.
- Part 1G done: `src/mm/mcp_kb.py` FastMCP server `knowledge-kb` with `search_knowledge`, `get_passage` (tenant check, malformed and missing IDs), and `get_brand_profile` (unapproved profiles return `PROFILE_PENDING_APPROVAL`, never the body). All tools are `readOnlyHint`, return `ErrorPayload` instead of raising, and carry `source_date` from the profile. `tests/test_mcp_kb.py` (11 tests, in-process client). Full suite: 84 passing.
  - Deviation from the spec: the spec asks for `collection_date`. We expose `source_date` from the brand profile instead, because a collection timestamp changes on every re-ingest and breaks the stable-ID rule.
  - Profiles are read at server start. Approving a profile needs a server restart.
- Part 1H done: `src/mm/records.py` (SQLite `customer_leads` per the spec DDL with CHECK constraints; `create_lead` rejects missing consent, unmasked contacts, and raw email or phone in the inquiry; `get_lead` is scoped by company). `tests/test_records.py` (11 tests): constraints reject bad rows, records is not imported by ingest, index, embed, search or bench, and lead text never appears in indexed passages. Full suite: 95 passing.
  - Scope note: only `customer_leads` is built. `product_offers` and `campaign_dispatches` from the spec DDL belong to the campaign-checks and publisher parts, outside this plan.
  - Synthetic leads use `expected_status` values (`qualified`, `routed_to_sales`) that the `status` CHECK does not allow. Lead qualification is not built yet, so nothing writes those values today.
- Part 2 done: campaign-checks (spec Module 3, server 2). `src/mm/campaign_checks.py` with `validate_channel` (channel limits from `config.yaml`, email subject and body separately, hashtag cap, empty content, unknown channel) and `check_offer_conflict` (product existence, date validation, cap by unit, overlap, bogo and bundle stacking with percentage offers). Both exposed as the read-only FastMCP server `campaign-checks`. Catalogues in `brand_packs/*/catalogue.json` seed `products` and `product_offers` tables in `records.db`. `tests/test_campaign_checks.py` (20 tests). Full suite: 115 passing.
  - Deviation: the spec puts `max_allowed_discount` on each offer row. We store the cap on the product, because a proposed offer has no cap of its own to compare against.
  - Deviation: the spec's `banned emojis` check is not built. The spec does not define the list, so this needs Dr. Ashutosh Verma's input.
  - Limitation: the cap applies only to percentage and flat discounts. Bogo and bundle values are not compared against a cap. The catalogue caps (10 percent for MOSETA, 25 percent for PulsePeak, 20 percent for TerraRoast) are placeholders pending business confirmation.
  - Thread-safety fix: FastMCP runs sync tools in a worker thread, so each tool call opens its own SQLite connection from the database path.
- Part 3 done: test-publisher (spec Module 3, server 4). `src/mm/publisher.py`:
  - Canonical JSON hash over the full payload, so any change to copy, schedule, destination or campaign ID invalidates approval.
  - Approval token = `expiry.HMAC-SHA256(secret, company|hash|expiry)`, checked with constant-time compare. Expired tokens return `APPROVAL_EXPIRED`. Forged or mismatched ones return `APPROVAL_HASH_MISMATCH`.
  - Destinations allowlisted to three mock destinations. Copy is re-validated with the campaign-checks rules.
  - Dispatch rows in `campaign_dispatches`. Replays are blocked by an idempotency key, by a `UNIQUE(company_id, payload_hash, channel_id)` constraint, and by a payload-hash check.
  - The publish tool is marked as a write (`readOnlyHint: False`). The server refuses to start without `HMAC_SECRET_KEY`.
  - `tests/test_publisher.py` (17 tests). Full suite: 134 passing.
- **Approval split (done):** issuing and verifying moved to `src/mm/approval.py`. The publisher imports only `verify_approval` and `CampaignPayload`. A test fails if the publisher imports `issue_approval`. Full suite: 135 passing.
  - **Limit of the split:** it is an import rule, not a security boundary. The publisher still needs `HMAC_SECRET_KEY` to verify, and any process that holds that secret can issue tokens. Real separation needs one of two things: (a) run the approval service as its own process holding the secret, with the publisher receiving only verification material; or (b) switch to asymmetric signatures (Ed25519), so the publisher holds a public key that cannot issue. The spec says HMAC-SHA256. Option (b) changes the spec, so it needs your decision.
  - The reviewer-facing approval API (spec `POST /api/v1/campaigns/approve`) is not built yet.
- Part 4 done: separate approval process (HMAC kept, matching the spec).
  - `src/mm/approval_service.py` is the only process that holds `HMAC_SECRET_KEY`. It runs on localhost. `POST /approve` issues a token for one exact payload and appends an audit line (reviewer, company, campaign, hash, expiry; no token, no secret). `POST /verify` checks a token, with expiry judged by the service's own clock.
  - `src/mm/publisher.py` no longer imports `hmac` or `approval_service` and never sees the secret. It verifies through `HttpVerifier`, which fails closed with `APPROVAL_SERVICE_UNAVAILABLE` when the service is unreachable.
  - Tests: `tests/test_approval_service.py` (11: service logic, HTTP round trip, tampering over HTTP, bad input, publish through the live service, refusal when unreachable). `tests/test_publisher.py` enforces that the publisher holds no secret and no issuer. Full suite: 146 passing.
  - Reviewer auth (done): `/approve` requires `Authorization: Bearer <token>`. The service maps the token to a reviewer ID through `REVIEWERS_FILE`, a JSON map of reviewer ID to SHA-256 of the token. The service stores only hashes, compares them in constant time, and ignores any reviewer ID in the request body. Unauthenticated requests return 401 and write no audit line. Full suite: 150 passing.
  - Publisher auth on `/verify` (done): `/verify` requires `Authorization: Bearer <PUBLISHER_TOKEN>`. The service stores only its hash. `PUBLISHER_TOKEN` is a service credential, separate from `HMAC_SECRET_KEY`, so the publisher still never holds the signing secret. Reviewer tokens cannot call `/verify`, and the publisher token cannot call `/approve`. The publisher's `HttpVerifier` fails closed on 401. Full suite: 156 passing.
  - Remaining gaps (see next entry for the fixes): the reviewer UI and login are not built. Tokens were plain bearer strings with no TLS and no rotation.
- Part 4c done: TLS and credential rotation.
  - `src/mm/credentials.py`: role-scoped credentials (`reviewer`, `publisher`) with expiry and revocation. The file stores SHA-256 hashes only. It is reread on every check, so revocation takes effect without a restart. CLI: `python -m src.mm.credentials issue | revoke | list`. Tokens are shown once at issue time.
  - `src/mm/approval_service.py` serves TLS only (TLS 1.2 minimum). It requires `HMAC_SECRET_KEY`, `CREDENTIALS_FILE`, `APPROVAL_TLS_CERT` and `APPROVAL_TLS_KEY`. Audit lines now record the credential ID.
  - `src/mm/publisher.py`'s `HttpVerifier` requires `https://`, takes an optional CA bundle (`APPROVAL_CA_BUNDLE`), and fails closed on an untrusted certificate.
  - `cryptography` is pinned in `requirements.txt`. Full suite: 171 passing.
  - Tests cover rotation with live revocation, expiry at the boundary, plain HTTP refused, untrusted certificate refused, and the CLI.
  - Remaining gaps: credential rotation is manual. TLS uses a self-signed certificate in tests; production needs a certificate issued by a real CA. The reviewer login UI is not built.
- Part 4d done: expiry alerts. `python -m src.mm.credentials check-expiry --file F --within-days 14` lists active, unrevoked credentials that have expired or expire within the window, and exits 2 when any are found, so cron or CI can alert on it. Nothing is sent anywhere; delivery of the alert is the scheduler's job. Full suite: 174 passing.
  - Gap: the check runs only when invoked. Nothing runs it on a schedule yet, and the service does not warn when a credential nears expiry.
- Retention clock built (`src/mm/retention.py`): sweeps leads (1 year unqualified, 3 years qualified), campaign dispatches (2 years), and ingested knowledge (1 year, stamped per passage at ingest). Brand profiles past 5 years are flagged for review, not deleted. Checkpoints and traces report `not_built`. Dry run by default; `--apply` deletes through `purge_lead` and the index. Full suite: 195 passing.
- Execution of the audit plan (started 2026-11-21):
  - F1 done: git initialised on `main`. Baseline commit `3821de5`. `.gitignore` excludes `.venv`, `storage/`, `data_output/`, `pptx_extracted/`, `.claude/`, and `project_details.md` (it contains a sample HMAC key from the spec).
  - T1 done: licence register in `source_inventory.md` section 5. Both repos are MIT. Python dependencies and both models are recorded. Open: confirm the E1 copyright holder by reading its LICENSE file, and check the MS MARCO dataset terms.
  - T2 done: every project data file (13) and every Drive reference file (10) is registered with its SHA-256. `tests/test_inventory.py` fails if a file is unregistered or its content has changed. Full suite: 209 passing. T1 and T2 are not committed yet.
  - T3 done: `src/mm/manifest.py` builds `source_manifest.json` at the project root (spec path was `data_input/source_manifest.json`; moved so the ingestion scan doesn't treat it as data). Every project data file must be declared, undeclared or missing files fail the build, and hashes are full SHA-256. `python -m src.mm.manifest --check` verifies it is current. `tests/test_manifest.py` (8 tests). Full suite: 217 passing. T3 is not committed yet.
  - T4 (Brand A licence decision) is still open and needs your decision.
- Retention sweep scheduled: a crontab entry runs a dry run daily at 02:17 local time. Reports go to `storage/reports/retention/YYYY-MM-DD_dryrun.json`, and cron output to `storage/reports/retention/cron.log`. The job never deletes. Deletion stays a manual `--apply` run after someone reads the report.
  - Risk to check: macOS may block cron from reading the Documents folder until cron is granted Full Disk Access. Confirm the first scheduled run wrote a report.
- Memory policy written (`memory_policy.md`, draft for Aniket's review).
- Cascade implemented (`src/mm/cascade.py`): `purge_company_data` covers relational, Chroma and BM25, and the trace interface. Layers 1 and 2 (LangGraph store and checkpoints) and the LangSmith backend are not built, so their receipts say so. `purge_lead` deletes the lead. Both are idempotent and verified. Full suite: 186 passing.
- Approval audit log: done (`storage/approval_audit.jsonl`, written by the approval service). Not built: a dispatch outbox file.
- Plan 1A–1H complete; Parts 2, 3, 4, 4c and 4d also complete. Not built from the spec: LangGraph orchestration and specialist agents, campaign planning, short- and long-term memory with cascade deletion, the FastAPI service and dashboard, Docker and monitoring, Reflexion loop, judge calibration, and the 60+ case evaluation. Prompt Guard is still a heuristic stub. Real embeddings and a meaningful Recall@5 need an OpenRouter key and paraphrase labels.
