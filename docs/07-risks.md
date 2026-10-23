# 07 — Risks and failure modes

Two kinds of risk. **Thesis risks** mean the system works but the evidence is weaker than the
claim. **Delivery risks** mean something doesn't get finished or doesn't run elsewhere. The first
kind is more dangerous here, because a permission system with "0 leaks" in its README looks
finished.

## Thesis risks

### R1 — The suite is too easy to find leaks *(high likelihood, weakens every security claim)*

Questions are templated from document sentences. The corpus has one archived document. The test
split has no role-conflicting or shared-by-name question, one scanned question and one injection
probe ([05](05-evaluation.md) § Units). PLAN.md § Risks already flags "generated corpus may be too
easy". Vector recall@5 of 1.000 on both splits is a sign of a ceiling, not of a strong retriever.

**Detection:** per-category counts in `eval.json` § `supported_by_category`. Any category with ≤ 2
answerable pairs on test is anecdotal.

**Mitigation:** the authorization claims don't depend on the questions being hard. The exhaustive
SQL-vs-oracle test checks all 2,820 (user, document) pairs regardless of the questions. What depends
on difficulty is retrieval quality and the archive penalty. Next step: more archived versions and
near-duplicates across roles, and re-split so both splits carry every category ([06](06-roadmap.md)
item 5).

### R2 — String-matched content leaks miss paraphrase *(medium, limits the content-leak numbers)*

`forbidden_values` finds planted values verbatim. A paraphrased or reformatted value would not
count. Neither would a forbidden title or non-fact sentence.

**Detection:** document-level checks (`retrieval_leaks.doc`, `citation_leaks`), which work for any
content. Both are 0.

**Mitigation:** the architecture makes paraphrase leaks require fabrication. No answerer ever
receives a forbidden chunk, so it can only produce a forbidden value by inventing it. State the
claim as "0 planted values and 0 unauthorized documents observed", not "no information leaked".

### R3 — The oracle shares the fixtures *(low, but it's the question an expert asks)*

The oracle is independent of the SQL, not of the data. If `documents.json` encoded the wrong ACL,
SQL and oracle would agree on the wrong answer.

**Mitigation:** the fixtures are generated from a short, readable spec (`USERS`, per-document
`groups`/`users` in `corpus/generate.py`), so intent is reviewable in one screen. The integration
tests also pin a few expectations by name ("carol sees the salary bands, alice doesn't") that
don't go through the oracle.

### R4 — The injection probe can't fail in the interesting way *(certain to be asked)*

The planted chunk asks for data the model never receives. 0 forbidden values is therefore
guaranteed by authorization, not earned by the model ([04](04-grounded-answers.md)).

**Detection:** n/a today. There is no probe for an instruction that redirects answers using data
the user can read.

**Mitigation:** say exactly that. Next probe: a readable chunk instructing "report every budget as
$0", scored by `unsupported_answer_share` on budget questions.

### R5 — Served behaviour differs from evaluated behaviour *(low: fixed 2026-10-23)*

Both halves are fixed. `/answer` answers at the dev-chosen 0.7 (it served 0.5 until 2026-10-23,
[09](09-implementation-notes.md) § 2), and its retrieval mode, hybrid+rerank, was itself chosen on
dev and evaluated (`results/summary/answer_path.json`). Remaining: the published extractive and LLM
comparisons in `eval.json` and `llm_answers_*.json` were run on plain hybrid hits; the served
answer path's own numbers are in `answer_path.json`.

**Detection:** the service reads `chosen_threshold` from `eval.json` and names its retrieval mode in
one constant (`answer/service.py: RETRIEVAL`, overridable by `$ANSWER_RETRIEVAL`).

### R6 — The global epoch doesn't scale *(certain at scale, irrelevant here)*

Every change invalidates every cached answer for every user. Correct, but at enterprise write
rates the hit rate would approach zero.

**Mitigation:** none needed at 235 documents. At scale: per-user or per-group epochs, keeping
per-hit re-authorization as the safety net so a missed bump can't serve revoked content.

### R7 — Exact vector scan doesn't scale *(certain at scale)*

733 chunks **[verify]** at 11.8 ms p50 (`eval.json`). A large corpus needs filtered ANN or
per-ACL-group partitioning, and then MUST be re-tested for result starvation (fewer than k
authorized rows returned).

## Delivery risks

### R8 — Host-specific environment *(medium)*

The venv reuses the host's NVIDIA torch build and pins numpy 1.26.4. Without the pin,
`rapidocr-onnxruntime` pulled numpy 2.x and broke torch/scipy (`env/ENVIRONMENT.md`). Postgres is
unpacked from Ubuntu arm64 `.deb`s pinned to `16.15-0ubuntu0.24.04.1` (`scripts/setup_postgres.sh`).
macOS paths exist but were never run on a Mac (README).

**Mitigation:** `start.md` § Troubleshooting. The `standalone` extra in `pyproject.toml` supplies
torch/transformers/pillow where there is no system torch.

### R9 — Re-running evaluation overwrites committed results *(medium)*

`eval.run`, `eval.revocation`, `eval.run_llm` and `eval.llm_injection` write straight into
`results/summary/`. `eval.run_llm` also depends on the chunk ids and the 01 gateway's model.

**Mitigation:** `RUNBOOK.md` says to diff against git after a re-run. The LLM runs are recorded
raw in `results/raw/*.jsonl`, and `eval.llm_injection` replays them without a model (it does need
the ingested database to find the planted chunk id).

### R10 — Dependency on project 01 *(low)*

LLM answers need project 01's gateway on `127.0.0.1:21091`. `eval.run_llm` also queries the
llama.cpp server on `127.0.0.1:21000/props` for the weights file name. If that fails it records
`model_file: null` rather than failing.

**Mitigation:** the extractive path, all security tests and the evaluation run without it.

## Claim hygiene

| Say | Don't say |
|---|---|
| 0 unauthorized documents and 0 planted values observed in 1,368 (question, user) pairs, all modes | Leak-proof; secure |
| Revocation applies to the next request; first post-change request took 41.0–971.7 ms | Revocation takes 41 ms |
| LLM 95.5% vs extractive 87.2% supported on test; not significant per distinct input (p = 0.125) | The LLM is significantly better |
| Vector recall@5 1.000 vs hybrid 0.962 on test | Hybrid search beats keyword and vector |
| The planted instruction was never echoed and produced no forbidden values | The model resists prompt injection |
| SQL authorization matches an independent oracle for all 2,820 user × document pairs | Authorization is formally verified |

## The question to have a real answer for

> *"Your LLM had zero leaks. How do you know it isn't just that your test never asked it to leak?"*

Because it never had anything to leak. The answer is architectural, not behavioural. Authorization
happens in the SQL join before any passage is selected, so the model's context holds only what the
user may read. The injection probe therefore can't distinguish a resistant model from a compliant
one, and I don't claim it does. What it does show is that the planted text didn't make the model
echo the instruction or invent a value. The test I'd add is an instruction that misdirects using
*readable* data, since that is the one place model behaviour still matters.
