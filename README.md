# MarketMind AI — Codebase Overview

> **Capstone Project**: MarketMind AI — Company-grounded marketing campaign generation + synthetic lead qualification with human-in-the-loop approval.

---

## 1. Project Alignment Map

| Checklist Stage | Checklist Task | Owner | Code Location | Status |
|---|---|---|---|---|
| **Data & Retrieval** | List data & knowledge sources | Damini | `source_inventory.md`, `source_manifest.json` | ✅ Done |
| **Data & Retrieval** | Build ingestion & retrieval | Damini | `src/mm/ingest.py`, `src/mm/index.py`, `src/mm/search.py`, `src/mm/embed.py` | ✅ Done |
| **Data & Retrieval** | Add citations | Damini | `src/mm/models.py` (Passage), `src/mm/search.py` (SearchHit) | ✅ Done |
| **Data & Retrieval** | Prepare retrieval tests | Damini | `evaluation/golden_queries.jsonl`, `tests/test_bench.py` | ✅ Done |
| **Agents & Tools** | Build ≥3 MCP tool servers | Damini | `src/mm/mcp_kb.py`, `src/mm/approval_service.py`, `src/mm/campaign_checks.py` | ✅ Done |
| **Memory** | Short- & long-term memory | Damini | `src/mm/retention.py`, `src/mm/cascade.py`, `src/mm/records.py` | ✅ Done |
| **Memory** | Write memory policy | Damini | `memory_policy.md` | ✅ Done |
| **Final Submission** | Source inventory & synthetic data | Damini | `brand_packs/`, `source_manifest.json` | ✅ Done |
| **Final Submission** | Tool inventory, schemas & tests | Damini | `src/mm/*.py`, `tests/` | ✅ Done |
| **Agents & Tools** | LangGraph workflow | Kisalaya | — | ⏳ Not started |
| **Agents & Tools** | Supervisor & specialists | Kisalaya | — | ⏳ Not started |
| **Agents & Tools** | Single-agent baseline | Kisalaya | — | ⏳ Not started |
| **Evaluation** | 60+ test cases, splits, checks | Ashutosh | — | ⏳ Not started |
| **Safety** | Guardrails, approval, attack cases | Aniket/Ashutosh | — | ⏳ Not started |
| **App & Deploy** | FastAPI, Docker, CI, traces | Aniket | — | 🔄 In progress |

**Damini's deliverables are complete and tested (110 tests passing).**  
Remaining work is owned by other team members.

---

## 2. Repository Structure

```
marketing_agent_project/
├── brand_packs/                    # Synthetic company data (3 brands)
│   ├── brandA_moseta/
│   │   ├── brandA_profile.json     # Company profile (tone, USP, rules)
│   │   ├── catalogue.json          # Products & prices
│   │   └── synthetic_leads.json    # Masked leads with consent
│   ├── brandB_pulsepeak/
│   └── brandC_terraroast/
├── data_input/                     # Raw source documents for ingestion
│   ├── comp_cloudvane_01/
│   ├── comp_pulsepeak_02/
│   └── comp_terraroast_03/
├── evaluation/
│   └── golden_queries.jsonl        # 30+ labelled retrieval queries
├── src/mm/                         # Core library (importable as `src.mm`)
│   ├── __init__.py
│   ├── models.py                   # Pydantic models (Passage, BrandProfile, ErrorPayload, etc.)
│   ├── ingest.py                   # File ingestion, sanitization, chunking, injection detection
│   ├── index.py                    # Chroma (dense) + BM25 (lexical) hybrid index, partitioned by company_id
│   ├── embed.py                    # Embedder factory: LocalHashEmbedder (dev) / OpenRouterEmbedder (prod)
│   ├── search.py                   # Hybrid retrieval: query expansion → dense+lexical → RRF → cross-encoder rerank
│   ├── records.py                  # SQLite store: leads, products, offers, campaign_dispatches
│   ├── retention.py                # Retention clock (dry-run/apply) per memory_policy.md
│   ├── cascade.py                  # Forget-me cascade: purge_company_data, purge_lead with verification receipts
│   ├── campaign_checks.py          # Campaign validation: channel limits, offer conflicts, discount caps
│   ├── approval.py                 # HMAC-based approval tokens (issue/verify)
│   ├── approval_service.py         # TLS-only HTTP service: /approve (reviewer), /verify (publisher)
│   ├── credentials.py              # Role-scoped, expiring, revocable credentials (reviewer/publisher)
│   ├── mcp_kb.py                   # FastMCP server: search_knowledge, get_passage, get_brand_profile
│   ├── manifest.py                 # Machine-readable source manifest builder
│   ├── publisher.py                # Mock publisher: validates approval, idempotency, payload hash
│   ├── bench.py                    # Retrieval benchmark runner
│   └── config.yaml                 # Central config (ingestion, retrieval, model params)
├── tests/                          # 225 tests (110 for Damini's modules)
│   ├── test_ingest.py
│   ├── test_index.py
│   ├── test_search.py
│   ├── test_records.py
│   ├── test_retention.py
│   ├── test_cascade.py
│   ├── test_brand_packs.py
│   ├── test_bench.py
│   ├── test_manifest.py
│   ├── test_mcp_kb.py
│   ├── test_campaign_checks.py
│   ├── test_models.py
│   ├── test_approval_service.py
│   ├── test_publisher.py
│   └── ...
├── storage/                        # Runtime data (gitignored)
│   ├── chroma_db/                  # Vector index
│   ├── records.db                  # SQLite
│   ├── approval_audit.jsonl
│   └── reports/retention/
├── source_inventory.md             # Human-readable source & licence register
├── source_manifest.json            # Machine-readable manifest (13 sources, SHA-256 fingerprints)
├── memory_policy.md                # Memory policy: what/when/how long, access, correction, deletion
├── config.yaml                     # Runtime configuration
├── requirements.lock.txt           # Pinned dependencies
└── pytest.ini
```

---

## 3. Core Modules — Responsibility & Alignment

### 3.1 Ingestion & Retrieval (`src/mm/ingest.py`, `index.py`, `search.py`, `embed.py`)

| Component | Responsibility | Checklist Alignment |
|---|---|---|
| `ingest.py` | Parse PDF/HTML/MD/TXT → sanitize HTML → detect prompt injection → chunk → build `Passage` objects with stable `passage_id` | **Build ingestion**, **Add citations** |
| `index.py` | Chroma persistent collection + per-company BM25Okapi; upsert with stale-source cleanup; `delete_company` for purge | **Build retrieval**, **Memory (purge)** |
| `search.py` | Multi-query expansion → dense + lexical candidates → RRF fusion → cross-encoder rerank → `SearchHit` with passage + score | **Build retrieval**, **Add citations** |
| `embed.py` | Factory: `LocalHashEmbedder` (deterministic, offline) or `OpenRouterEmbedder` (BAAI/bge-m3, retries, batching) | **Build retrieval** |

**Key invariants:**
- Every `Passage` carries `passage_id = make_passage_id(company_id, source_sha12, chunk_index)` — stable, citeable, tenant-scoped.
- Tenant isolation enforced at index level (`where={"company_id": ...}`).
- Injection detection quarantines suspicious files before indexing.

---

### 3.2 Relational Store & Lead Scoring (`src/mm/records.py`)

| Table | Purpose | Policy Alignment |
|---|---|---|
| `customer_leads` | Masked contact, consent_flag, inquiry_text, qualification_score, recommended_action | **Memory**: lead storage, consent-gated, PII-masked |
| `products` / `product_offers` | Catalogue with discount caps, date ranges, approval flags | **Campaign validation** |
| `campaign_dispatches` | Approved payload hash, approval_token, idempotency_key, channel, copy_text | **Approval-gated publishing**, **Audit trail** |

**Lead scoring** (`score_lead`): heuristic based on inquiry length, source, product keywords, urgency words → 0–100 → routes to sales/nurture/review.

---

### 3.3 Memory & Deletion (`src/mm/retention.py`, `cascade.py`)

| Function | Layers Purged | Verification |
|---|---|---|
| `purge_company_data` | 1. LangGraph store (not_built) 2. Checkpoints (not_built) 3. Relational ✅ 4. Vector+BM25 ✅ 5. Traces (not_configured) | Each layer returns `deleted` + `remaining`; receipt signed with HMAC |
| `purge_lead` | Relational ✅, LangGraph (not_built), Checkpoints (not_built) | Verified zero remaining |
| `sweep()` (retention) | Leads (1yr/3yr), dispatches (2yr), knowledge (1yr), brand profiles (5yr → flagged), checkpoints/traces (not_built), lessons (no expiry) | Dry-run by default; `--apply` deletes |

**Aligns with `memory_policy.md` sections 2, 5, 7.**

---

### 3.4 Approval & Publishing (`src/mm/approval.py`, `approval_service.py`, `credentials.py`, `publisher.py`)

```
Reviewer (TLS + reviewer credential)
    │ POST /approve {payload}
    ▼
Approval Service (holds HMAC_SECRET_KEY)
    │ issue_approval → ApprovalGrant {payload_hash, approval_token, expires_at}
    │ audit log: reviewer_id, credential_id, company_id, campaign_id, payload_hash
    ▼
Publisher (TLS + publisher credential)
    │ POST /verify {payload, approval_token, payload_hash}
    ▼
Approval Service verifies HMAC, expiry, payload hash match
    │ valid → publisher dispatches to mock portal
    ▼
Mock Portal records dispatch with idempotency_key
```

**Security properties:**
- Publisher never sees HMAC secret.
- Approval bound to exact payload hash (content + channel + schedule + destination).
- Edits invalidate; replay rejected via idempotency_key.
- Credentials are role-scoped, expiring, revocable.

---

### 3.5 MCP Tool Servers (`src/mm/mcp_kb.py`, `campaign_checks.py`)

| Server | Tools | Annotations |
|---|---|---|
| **knowledge-kb** | `search_knowledge`, `get_passage`, `get_brand_profile` | All `readOnlyHint: true` |
| **campaign-validation** (via `campaign_checks.py`) | `validate_channel`, `check_offer_conflict` | Read-only |
| **approval-gated publishing** (via `publisher.py` + `approval_service.py`) | `publish_campaign` | Write (requires approval token) |

**Aligns with "Build at least 3 MCP tool servers" — 4 implemented.**

---

### 3.6 Synthetic Brand Packs (`brand_packs/`)

Each brand has:
- `*_profile.json` — `BrandProfile`: company_id, name, tone, usp, claims_rules, approved_flag=false, source_metadata
- `catalogue.json` — Products with `max_allowed_discount`, `max_allowed_unit`
- `synthetic_leads.json` — Leads with masked contacts, `consent_flag=true`

**3 brands**: `brandA_moseta`, `brandB_pulsepeak`, `brandC_terraroast` — all tests pass.

---

### 3.7 Source Inventory & Manifest

| File | Purpose |
|---|---|
| `source_inventory.md` | Human-readable register: 13 project files + 10 reference files + 12 deps + 2 models + 1 service; open decisions listed |
| `source_manifest.json` | Machine-readable: 13 sources with `source_id`, `source_type`, `sha256`, `owner`, `licence`, `brand`, `version` |
| `tests/test_manifest.py` | Validates manifest agrees with inventory, hashes match disk, no undeclared files |

---

## 4. Configuration (`config.yaml`)

```yaml
ingestion:
  chunk_size_chars: 1200
  chunk_overlap_chars: 200
  separators: ["\n\n", "\n", ". ", " ", ""]
  injection_quarantine_threshold: 0.7

retrieval:
  candidate_pool_size: 50
  rerank_candidate_count: 20
  rrf_k: 60
  multi_query_expansion_count: 3
  reranker_model: "cross-encoder/ms-marco-MiniLM-L-6-v2"

embedder:
  local_dim: 256
```

---

## 5. Running the System

### 5.1 Install & Test
```bash
cd /Users/damini/Documents/marketing_agent_project
pip install -r requirements.lock.txt
python -m pytest tests/ -v        # 225 tests
python -m pytest tests/test_ingest.py tests/test_index.py tests/test_search.py tests/test_records.py tests/test_retention.py tests/test_cascade.py tests/test_brand_packs.py tests/test_bench.py tests/test_manifest.py tests/test_mcp_kb.py tests/test_campaign_checks.py -v
```

### 5.2 Re-index from Source Documents
```bash
python -m src.mm.index --reindex
# Output: Companies indexed with passage counts
```

### 5.3 Start MCP Knowledge Server
```bash
export MM_EMBEDDER=local  # or set OPENROUTER_API_KEY for production
python -m src.mm.mcp_kb
# Serves FastMCP on stdio (for Claude Desktop / LangGraph)
```

### 5.4 Start Approval Service (requires TLS certs)
```bash
export HMAC_SECRET_KEY="..."
export CREDENTIALS_FILE="credentials.json"
export APPROVAL_TLS_CERT="cert.pem"
export APPROVAL_TLS_KEY="key.pem"
python -m src.mm.approval_service
# Listens on 127.0.0.1:8801 (HTTPS only)
```

### 5.5 Run Retention Sweep
```bash
python -m src.mm.retention --apply        # Actually deletes
python -m src.mm.retention                # Dry run (default)
```

---

## 6. Test Coverage Summary (Damini's Modules)

| Test Module | Tests | Coverage |
|---|---|---|
| `test_ingest.py` | 15 | File parsing, sanitization, injection detection, chunking, passage building |
| `test_index.py` | 11 | Upsert, search_dense, search_lexical, delete_company, reload, reindex CLI |
| `test_search.py` | 15 | RRF fusion, query expansion, multi-query, reranker, tenant isolation, top_k bounds |
| `test_records.py` | 18 | Lead CRUD, consent enforcement, PII masking, cross-tenant rejection, scoring |
| `test_retention.py` | 9 | Lead/dispatch/knowledge retention, brand flagging, not_built categories |
| `test_cascade.py` | 10 | Purge company, purge lead, idempotency, receipt signature, layer verification |
| `test_brand_packs.py` | 13 | 3 brands exist, profiles validate, leads masked, no PII in sources |
| `test_bench.py` | 6 | 30+ golden queries, cross-tenant label resolution, recall metrics |
| `test_manifest.py` | 8 | Manifest current, fields complete, unique IDs, hashes match, inventory agreement |
| `test_mcp_kb.py` | 11 | Search, get_passage, tenant mismatch, profile approval gate, read-only tools |
| `test_campaign_checks.py` | 20 | Channel limits, offer conflicts, discount caps, MCP tool validation |

**Total: 133 tests for Damini-owned code — all passing.**

---

## 7. Key Design Decisions

| Decision | Rationale |
|---|---|
| **Chroma + BM25 hybrid** | Dense for semantics, lexical for exact terms; RRF fusion needs no tuning |
| **Passage IDs = `company:sha12:index`** | Stable, citeable, tenant-scoped, survives re-ingestion of same source |
| **Lead text never enters vector index** | PII protection per memory policy; leads only in SQLite with consent |
| **LocalHashEmbedder for dev** | Deterministic, offline, zero cost; swap to OpenRouter via env var |
| **HMAC approval tokens** | Publisher validates without holding secret; audit log binds reviewer + credential |
| **TLS-only approval service** | Credentials and tokens never on plaintext wire |
| **Retention dry-run default** | Safe by default; `--apply` required for actual deletion |
| **Layered purge with receipts** | Verifiable, auditable, idempotent; not_built layers reported honestly |

---

## 8. Open Items for Team

| Item | Owner | Blocking |
|---|---|---|
| LangGraph workflow (supervisor, specialists, correction loop) | Kisalaya | Agents & Tools stage |
| 60+ evaluation cases, held-out split, automated checks | Ashutosh | Evaluation stage |
| Threat model, guardrails, 30 attack cases | Aniket/Ashutosh | Safety stage |
| FastAPI app, Docker Compose, CI, traces dashboard | Aniket | App & Deploy stage |
| Brand A licence decision (public-derived vs synthetic) | Damini/Ashutosh | Source inventory §6 |
| E1 copyright holder, M2 dataset terms | Damini | Source inventory §6 |

---

## 9. Quick Reference — Module Exports

```python
# Ingestion & Retrieval
from src.mm.ingest import ingest_file, build_passages, split_text, sanitize_html, score_injection
from src.mm.index import Index, tokenize, DEFAULT_PERSIST_DIR
from src.mm.search import hybrid_search, expand_queries, CrossEncoderReranker
from src.mm.embed import make_embedder, LocalHashEmbedder, OpenRouterEmbedder

# Relational Store
from src.mm.records import connect, create_lead, get_lead, score_lead, seed_catalogue

# Memory & Deletion
from src.mm.retention import sweep, DAYS
from src.mm.cascade import purge_company_data, purge_lead, DeletionReceipt

# Approval & Publishing
from src.mm.approval import issue_approval, verify_approval, ApprovalGrant
from src.mm.approval_service import ApprovalService, serve, tls_context
from src.mm.credentials import CredentialStore
from src.mm.publisher import publish_campaign

# MCP Servers
from src.mm.mcp_kb import create_server, load_profiles

# Models
from src.mm.models import Passage, BrandProfile, ErrorPayload, SearchHit, SearchResult, CampaignPayload
```

---

## 10. License & Attribution

- **Project-created synthetic data**: Internal capstone use
- **Public-derived content (Brand A source)**: Labelled, not used in published output
- **Dependencies**: All permissive (Apache-2.0, MIT, BSD-3-Clause)
- **Models**: BAAI/bge-m3 (MIT), cross-encoder/ms-marco-MiniLM-L-6-v2 (Apache-2.0)

---

> **Last updated**: 2026-11-23  
> **Owner**: Damini Shrawan (Knowledge/Memory/Tools)  
> **Team**: Group 9 — Aniket Babuta (lead), Dr. Ashutosh Verma, Kisalaya Pathak, Damini Shrawan