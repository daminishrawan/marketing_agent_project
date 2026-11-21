# MarketMind AI — Source & Licence Inventory

**Owner:** Damini Shrawan
**Status:** In progress
**Last updated:** 2026-11-20

---

## 1. Purpose

This document lists every data and knowledge source used by MarketMind AI, including ownership, licence, date, brand, and version. It feeds the ingestion pipeline and the retrieval benchmark.

---

## 2. Data Policy

- **No real customer data.** All lead data is synthetic.
- **No live platform publishing.** All outputs are drafts and mock publications.
- **Website claims are labelled** as `website-listed` and `requires verification` until approved.
- **External-source licences** are recorded for every third-party document.

---

## 3. Source Inventory

### 3.1 Synthetic Brand Packs (created for this project)

| # | Source | Owner | Licence | Date | Brand | Version | Usage |
|---|--------|-------|---------|------|-------|---------|-------|
| 1 | Brand Pack A — MOSETA (Solar AC / Clean Energy) | Damini Shrawan | Project-created (synthetic) | 2026-11 | MOSETA | v1.0 | Knowledge base, retrieval, content drafts |
| 2 | Brand Pack B — PulsePeak (fitness clubs) | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | PulsePeak | v1.0 | Knowledge base, retrieval, content drafts |
| 3 | Brand Pack C — TerraRoast (coffee roastery) | Damini Shrawan | Project-created (synthetic) | 2026-11-21 | TerraRoast | v1.0 | Knowledge base, retrieval, content drafts |

**Synthetic leads** (not ingested; stored in `brand_packs/<brand>/synthetic_leads.json`): 2 per brand, masked contacts, `consent_flag` set.

**Open licence issue:** Brand Pack A's source text (`data_input/comp_cloudvane_01/brandA_moseta_source.txt`) was summarised from the public MOSETA website (`moseta.in`), while Brand Pack A is labelled project-created synthetic. Decide before use: either keep it as summarised public content with a licence record (row 4), or replace it with fully invented content. Until decided, treat Brand A as `public-summarised`.

**Per-brand contents required:**
- Company catalogue (products, specs, pricing)
- Brand tone and voice guidelines
- Claims restrictions (what cannot be advertised without approval)
- Active offers and promotions
- Lead examples (synthetic)
- Source metadata (passage IDs, source type, date)

### 3.2 Existing Reference Data

| # | Source | Owner | Licence | Date | Brand | Version | Usage |
|---|--------|-------|---------|------|-------|---------|-------|
| 4 | `moseta_website_content.docx` | Ashutosh Verma | Public website content (summarised) | 2026-11-17 | MOSETA | v1.0 | Reference for Brand Pack A structure |
| 5 | `knowledge_base.json` (x1 output) | Ashutosh Verma | Project-created | 2026-11-17 | MOSETA / TVS | v1.0 | Baseline knowledge base format |
| 6 | `retrieval_index.json` (x3 output) | Ashutosh Verma | Project-created | 2026-11-17 | MOSETA / TVS | v1.0 | Baseline retrieval index format |

### 3.3 Third-Party / External

| # | Source | Owner | Licence | Date | Brand | Version | Usage |
|---|--------|-------|---------|------|-------|---------|-------|
| 7 | — | — | — | — | — | — | None yet. Record any external source here. |

---

## 4. Source Types

| Type | Description | Examples |
|------|-------------|----------|
| `company_profile` | Structured company/product fields | company_name, products, usp, tone |
| `source_text` | Raw or summarised document text | Website content, FAQs, past campaigns |
| `brand_rules` | Tone, claims restrictions, allowed themes | Marketing rules for MarketMind AI |
| `lead_data` | Synthetic lead records | Mock inbox, lead forms |

---

## 5. Ingestion Requirements

Each source must be tagged with:

- `source_id` — unique identifier (e.g., `brandA:products:001`)
- `source_type` — one of the types above
- `source_ref` — file path or URL reference
- `source_date` — when the content was retrieved/created
- `owner` — who provided the content
- `licence` — usage terms
- `brand` — which synthetic brand it belongs to
- `version` — version of the source document

---

## 6. Next Steps

- [x] Create Brand Pack B (synthetic company)
- [x] Create Brand Pack C (synthetic company)
- [ ] Assign passage IDs to all chunks in Brand Pack A (happens at ingestion, Part 1C)
- [ ] Resolve Brand Pack A licence issue above
- [ ] Confirm licence for any external source before ingestion
- [ ] Pass inventory to Ashutosh for consistency review