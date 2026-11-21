# MarketMind AI — Memory Policy

**Owner:** Damini Shrawan (author) · **Reviewer:** Aniket Babuta (access, retention, traces, backups, deletion coverage)
**Version:** 0.1 draft · **Date:** 2026-11-21 · **Status:** Draft for review. Not legal advice.

## 1. Scope and principles

MarketMind AI is a capstone prototype. It uses synthetic company packs and synthetic leads only. No real customer personal data is stored. The rules below are written to the principles of India's Digital Personal Data Protection Act 2023 (DPDP Act) and the GDPR, so the design would transfer to real data. Clause references are for Aniket to verify against the current Act and Rules.

- **Purpose limitation:** each record is kept only for the purpose named in section 2.
- **Storage limitation:** each record has a retention limit and is deleted after it.
- **Consent:** lead records are stored only with `consent_flag = true`.
- **Data minimisation:** leads store a masked contact handle and never the raw email or phone number. Lead text never enters the vector index.
- **Tenant isolation:** every record is keyed by `company_id`. No query crosses a company boundary.

## 2. What is saved, when, and for how long

| Data | Saved when | Purpose | Retention | Deleted by |
|---|---|---|---|---|
| Ingested knowledge (passages, metadata, BM25 tokens) | Upload or crawl completes | Grounded answers and citations | 1 year | `purge_company_data` |
| Brand profile | Human sign-off (`approved_flag = true`) | Tone and claim rules for all agents | 5 years | `purge_company_data` |
| Conversation checkpoints | Each graph step, key `{company_id}:{session}` | Resume an interrupted run | 30 days rolling | Auto-prune or `purge_company_data` |
| Lead records (`records.db`) | Lead ingested with consent | Qualification and routing | 1 year if unqualified, 3 years if a client | `purge_lead(lead_id)` or cascade |
| Approved Reflexion lessons | Human approves the lesson | Improve future runs | Indefinite, until revoked by an admin | Admin revocation |
| Campaign history and dispatch records | Human approval and dispatch | Audit and versioning | 2 years | `purge_company_data` |
| Execution traces | Live streaming | Debugging and observability | 90 days, with inputs masked | Tag-based purge |

**Not stored:** raw lead contact details, real customer data, the content of approval tokens or the HMAC secret, and reviewer credentials. Approval audit lines store the reviewer ID, the credential ID, and the payload hash.

## 3. Access boundaries

| Store | Who may read | Who may write |
|---|---|---|
| Ingested knowledge | Knowledge specialist | Ingestion pipeline only |
| Brand profile | All agents, approved profiles only | Human reviewer, via the approval path |
| Checkpoints | Checkpointer runtime | LangGraph runtime |
| Lead records | Lead specialist | Lead ingestion, lead scoring |
| Campaign history | Content specialist | Publisher, after approval |
| Reflexion lessons | Supervisor and specialists | Human reviewer only |
| Traces | Observability, admins | Runtime only, through the masking hook |

- The publisher is the only agent that can cause an external effect. It is blocked from dispatch without a valid approval.
- Only the approval service holds the signing secret. The publisher and the agents never receive it.
- Agent tools are scoped per agent. Each tool is marked read or write, and write access goes only to the agent that needs it.

## 4. Correction

- A source file that changes gets a new content hash. Its old passage IDs are retired and its new passages replace them. Re-ingestion is idempotent.
- A brand profile is corrected by the human reviewer. Each correction makes the old approval invalid, so the profile must be approved again.
- A lead is corrected by deleting it and creating a new record. Leads are not edited in place.

## 5. Deletion: the "Forget Me" cascade

Deletion is idempotent and retryable. Each layer is verified after it runs. Repeating a call on an empty company returns zero deletions and no error.

**`purge_company_data(company_id)`** runs five layers in order:
1. **LangGraph store:** evict every namespace for the company. Verify zero keys remain.
2. **Checkpoint database:** delete rows where the thread ID starts with `{company_id}:`. Verify zero rows remain.
3. **Relational store (`records.db`):** hard-delete the company's rows in `customer_leads`, `product_offers`, `products` and `campaign_dispatches`. Verify zero rows remain.
4. **Vector store and BM25:** delete every chunk where `company_id` matches, then rebuild the lexical index for that company. Verify zero chunks remain.
5. **Traces (LangSmith):** mask inputs before logging from now on. Delete existing runs tagged with the company ID.

It then returns a receipt: `company_id`, layers purged, deleted counts per layer, timestamp, and a signature.

**`purge_lead(lead_id)`** deletes that lead from `customer_leads`, its pseudonymous mapping in the store, and any checkpoint entries that reference it. It then verifies that none remain.

**Backups and logs:** the audit log and backups are the open items for Aniket. A deleted record must not reappear from a restored backup. Backup retention and how purged data is excluded from restores are to be decided.

## 6. Open items

- Aniket to confirm backup and log retention and the restore behaviour.
- Confirm the exact DPDP Act and Rules clauses cited above.
- Decide whether a subject-access export is needed for the demo. It is not built.
- Brand Pack A's source came from a public website. The licence question in `source_inventory.md` is still open.

## 7. Implementation status

| Item | Status |
|---|---|
| Ingested knowledge: purge from Chroma and BM25 | Built (`Index.delete_company`), tested |
| Relational purge: leads, offers, products, dispatches | Built (`src/mm/cascade.py`), tested |
| `purge_company_data` with verification and receipt | Built. Layers 1 and 2 report `not_built` |
| `purge_lead` with verification | Built. Store and checkpoint layers report `not_built` |
| Receipt signature (HMAC over the receipt digest) | Built, when a signing key is supplied |
| Lead records and consent check | Built (`src/mm/records.py`) |
| Approval audit log | Built |
| LangGraph store and checkpoints (layers 1 and 2) | Not built |
| Trace masking and tag-based deletion (layer 5) | Interface built. No LangSmith backend, so it reports `not_configured` |
| Retention clock: leads, dispatches, ingested knowledge | Built (`src/mm/retention.py`). Dry run by default. `--apply` deletes. Tested |
| Retention clock: brand profiles past 5 years | Flagged for human review, never deleted automatically |
| Retention clock: checkpoints and traces | Not built, reported as `not_built` |
| Retention: Reflexion lessons | No expiry, by policy |

**Assumption to confirm:** the policy's "client" lead category is mapped to leads with `status = qualified`. The 3-year limit applies to those only.
