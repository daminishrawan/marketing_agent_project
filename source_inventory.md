# MarketMind AI — Source & Licence Inventory

**Owner:** Damini Shrawan
**Status:** Register complete. Brand A licence decision open (see section 6).
**Last updated:** 2026-11-21

---

## 1. Purpose

This document lists every data, knowledge and third-party source used by MarketMind AI, with owner, licence, date, brand and version. Each project file is fingerprinted with its SHA-256 (first 12 hex characters). A test fails if a file changes without this register being updated.

---

## 2. Data Policy

- **No real customer data.** All lead data is synthetic.
- **No live platform publishing.** All outputs are drafts and mock publications.
- **Website claims are labelled** as `website-listed` and `requires verification` until approved.
- **Public-derived content** is never presented as synthetic. It is labelled `public-derived` and kept out of any published output.
- **External-source licences** are recorded for every third-party item below.

---

## 3. Project Data Files (in this repository)

These files are checked by `tests/test_inventory.py`.

| # | File | Brand | Type | Owner | Licence | Date | Version | SHA-256 |
|---|------|-------|------|-------|---------|------|---------|---------|
| 1 | `data_input/comp_cloudvane_01/brandA_moseta_source.txt` | MOSETA | source_text | Damini Shrawan | **Open (section 6): public-derived, summarised from moseta.in** | 2026-11-20 | v1.0 | c4ecec35cc79 |
| 2 | `brand_packs/brandA_moseta/brandA_profile.json` | MOSETA | company_profile | Damini Shrawan | Project-created (synthetic), pending decision on #1 | 2026-11-20 | 1.0 | 7341c14ed39c |
| 3 | `brand_packs/brandA_moseta/catalogue.json` | MOSETA | catalogue | Damini Shrawan | Project-created (synthetic) | 2026-11-20 | 1.0 | ca64ad78f119 |
| 4 | `brand_packs/brandA_moseta/synthetic_leads.json` | MOSETA | lead_data | Damini Shrawan | Project-created (synthetic) | 2026-11-20 | 1.0 | 366401111ef0 |
| 5 | `data_input/comp_pulsepeak_02/facilities_and_terms.md` | PulsePeak | source_text | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | v1.0 | 60316bcd4b57 |
| 6 | `brand_packs/brandB_pulsepeak/brandB_profile.json` | PulsePeak | company_profile | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | 1.0 | 7106840af64f |
| 7 | `brand_packs/brandB_pulsepeak/catalogue.json` | PulsePeak | catalogue | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | 1.0 | a9f01919e79d |
| 8 | `brand_packs/brandB_pulsepeak/synthetic_leads.json` | PulsePeak | lead_data | Damini Shrawan | Project-created (synthetic), consent true | 2026-11-21 | 1.0 | 5171c6313b9c |
| 9 | `data_input/comp_terraroast_03/bean_origins_and_catalog.md` | TerraRoast | source_text | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | v1.0 | c416b8807978 |
| 10 | `brand_packs/brandC_terraroast/brandC_profile.json` | TerraRoast | company_profile | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | 1.0 | 056029b90a6a |
| 11 | `brand_packs/brandC_terraroast/catalogue.json` | TerraRoast | catalogue | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | 1.0 | 587f90c3be62 |
| 12 | `brand_packs/brandC_terraroast/synthetic_leads.json` | TerraRoast | lead_data | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | 1.0 | 89c727c81077 |
| 13 | `evaluation/golden_queries.jsonl` | All three | evaluation_labels | Damini Shrawan (drafted); Dr. Ashutosh Verma to review | Project-created. Self-drafted, not evaluation-grade | 2026-11-21 | 1.0 | 3294f4e6a479 |

Notes:
- Synthetic leads are **not ingested** into the search index. They are stored in `records.db` only after consent checks.
- Brand profiles stay unapproved (`approved_flag = false`) until a human signs them off.

---

## 4. Reference Files (Drive folder, not in this repository)

These are kept for provenance. They are not used as input to the pipeline unless noted.

| # | File | Owner | Licence | Date | Brand | SHA-256 | Use |
|---|------|-------|---------|------|-------|---------|-----|
| R1 | `Ashutosh Verma/data_input/moseta_website_content.docx` | Ashutosh Verma | **Public-derived.** Copyright of MOSETA website content. Reference only | 2026-11-17 | MOSETA | 0b15d1091e21 | Structure reference for Brand A |
| R2 | `data_output/knowledge_base.json` | Ashutosh Verma | **Public-derived.** `source_text` copied from moseta.in. `company_profile` describes a different brand (TVS/ebike) | 2026-11-17 | MOSETA / TVS (conflicting) | 23c17c463037 | **Do not use `company_profile`.** Format reference only |
| R3 | `data_output/retrieval_index.json` | Ashutosh Verma | Derived from R2 (public-derived) | 2026-11-17 | MOSETA / TVS | cf52ca501b4a | Format reference only |
| R4 | `data_output/retrieval_test_results.json` | Ashutosh Verma | Derived from R2 (public-derived) | 2026-11-17 | MOSETA / TVS | ad44fadd3d60 | Historical baseline output |
| R5 | `data_output/content_drafts.json` | Ashutosh Verma | Derived from R2 (public-derived). Unapproved drafts | 2026-11-17 | MOSETA / TVS | 530d18ed8884 | Not used |
| R6 | `x1.py` (identical copy `x1 (1).py`) | Ashutosh Verma | Team code. **No licence stated.** Internal capstone use | 2026-11-17 | — | ec3b6e1f6390 | Front GUI reference |
| R7 | `x2.py` | Ashutosh Verma | Team code. No licence stated | 2026-11-17 | — | 666944f3a4ef | Content studio reference |
| R8 | `x2 (1).py` | Ashutosh Verma | Team code. No licence stated. Differs from R7 | 2026-11-17 | — | 24fbed32cb48 | Unreviewed variant |
| R9 | `x3.py` | Ashutosh Verma | Team code. No licence stated | 2026-11-17 | — | 12305cbc7faa | Retrieval baseline reference |
| R10 | `x3 (1).py` | Ashutosh Verma | Team code. No licence stated. Differs from R9 | 2026-11-17 | — | 9406aef15ea4 | Unreviewed variant |

---

## 5. Third-Party and External Sources

### 5.1 Open-source repositories (reference only; no code copied)

| # | Source | Licence | Copyright holder (as read) | Checked | Use |
|---|--------|---------|----------------------------|---------|-----|
| E1 | `github.com/SamurAIGPT/Open-Pomelli` | MIT | "Marketing Agent Contributors", 2024. **Confirm by opening the LICENSE file** (name looks like a template) | 2026-11-21 | Design reference |
| E2 | `github.com/patchy631/ai-engineering-hub` | MIT | patchy631, 2024 | 2026-11-21 | Design reference |

### 5.2 Python dependencies (installed versions from `requirements.lock.txt`)

| # | Package | Version | Licence |
|---|---------|---------|---------|
| P1 | fastmcp | 4.2.3 | Apache-2.0 |
| P2 | chromadb | 1.5.9 | Apache-2.0 |
| P3 | rank-bm25 | 0.2.2 | Apache-2.0 |
| P4 | sentence-transformers | 5.7.0 | Apache-2.0 |
| P5 | pydantic | 2.14.4 | MIT |
| P6 | pyyaml | 6.0.4 | MIT |
| P7 | pypdf | 7.1.1 | BSD-3-Clause |
| P8 | beautifulsoup4 | 4.15.0 | MIT |
| P9 | requests | 2.34.2 | Apache-2.0 |
| P10 | numpy | 2.5.5 | BSD-3-Clause and others (permissive) |
| P11 | pytest | 9.2.0 | MIT |
| P12 | cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |

### 5.3 Models

| # | Model | Licence (model card metadata) | Use | Open point |
|---|-------|-------------------------------|-----|-----------|
| M1 | `BAAI/bge-m3` | MIT | Embeddings via OpenRouter | OpenRouter's terms also apply. Not yet checked |
| M2 | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Apache-2.0 | Reranker on CPU | Its training data is MS MARCO. Check MS MARCO dataset terms before any commercial use |

### 5.4 Services

| # | Service | Terms | Use |
|---|---------|-------|-----|
| S1 | OpenRouter | Provider terms, not yet recorded | Hosted embeddings and LLMs. Not yet used |

---

## 6. Open Decisions

- **Brand A licence (T4):** item #1 is summarised from a public website, but items #2 to #4 are labelled synthetic. Choose one before any use:
  - keep #1 as `public-derived` with this record, or
  - replace #1 with invented content.
  Until decided, Brand A is treated as `public-derived`.
- **E1 copyright holder:** open the LICENSE file on GitHub and copy the holder name exactly.
- **M2 dataset terms:** confirm the MS MARCO terms before any commercial use.

---

## 7. Source Types

| Type | Description | Examples |
|------|-------------|----------|
| `company_profile` | Structured company/product fields | company_name, products, usp, tone |
| `source_text` | Raw or summarised document text | Website content, FAQs, past campaigns |
| `catalogue` | Products, prices, specifications | Product lists with prices |
| `brand_rules` | Tone, claims restrictions, allowed themes | Marketing rules |
| `lead_data` | Synthetic lead records | Mock inbox, lead forms |
| `evaluation_labels` | Labelled queries for the benchmark | Golden queries |

---

## 8. Ingestion Requirements

Each source must be tagged with:

- `source_id` — unique identifier (e.g., `brandA:products:001`)
- `source_type` — one of the types above
- `source_ref` — file path or URL reference
- `source_date` — when the content was created
- `owner` — who provided the content
- `licence` — usage terms
- `brand` — which synthetic brand it belongs to
- `version` — version of the source document
- `sha256` — fingerprint of the file content (see section 3)

---

## 9. Next Steps

- [x] Create Brand Pack B (synthetic company)
- [x] Create Brand Pack C (synthetic company)
- [x] Register every project file with a fingerprint (section 3)
- [x] Register reference files and third-party licences (sections 4 and 5)
- [ ] Decide the Brand A licence (section 6)
- [ ] Confirm E1 copyright holder and M2 dataset terms (section 6)
- [x] Generate a machine-readable manifest: `source_manifest.json` at the project root, built by `python -m src.mm.manifest` (T3). It sits outside `data_input/` so the ingestion scan and the inventory test don't treat it as source data
- [ ] Pass inventory to Dr. Ashutosh Verma for consistency review
