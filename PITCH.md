# ArchitectOS — OpenAI Build Week submission kit

**Deadline: July 21, 2026, 5:00 pm PT** (submissions on Devpost). Video must be
**< 3 minutes**, public on YouTube, with clear audio explaining how you used
**GPT-5.6 and Codex**.

---

## Devpost description (paste-ready)

**Tagline:** The AI architect that knows your whole codebase.

**The problem.** AI coding assistants are goldfish: they see the current prompt, not
the organization's reality. They generate code that ignores existing APIs, data models,
design decisions, and the docs that explain *why*. The result is plausible code that
breaks real systems — and engineers still do impact analysis by hand before every
non-trivial change.

**What ArchitectOS does.** Point it at a repository and it builds a **persistent
knowledge graph**: every file, function, class, API endpoint, data model, and ADR,
connected by typed edges (IMPORTS, CALLS, EXPOSES, CALLS_API, USES_MODEL, DOCUMENTS…).
The graph spans the whole stack — a frontend `fetch` is linked to the backend endpoint
it calls, down to the database model behind it.

Then GPT-5.6 and Codex reason **over the graph**:

- **Ask** anything — answers are grounded in retrieved graph context and cite real
  nodes; clicking a citation lights it up in the interactive graph.
- **Impact analysis** — describe a change ("Add OAuth login with Google") and the
  engine computes the **blast radius** deterministically from graph edges: 110 nodes
  across 13 services, ranked seed/high/medium/low, with a human-readable *why* for
  every node. GPT-5.6 turns that radius into a staff-engineer implementation plan —
  steps, risks, rollout, test plan, docs to update.
- **Codegen** — gpt-5.3-codex writes the implementation, migration, tests, and doc
  updates, grounded in the radius and real source snippets, streamed file by file.
- **Architecture brief** — module map, key flows, data model, coupling hot-spots, and
  prioritized recommendations, plus Mermaid diagram source.

**How we used OpenAI.** GPT-5.6 (Sol) powers reasoning: grounded Q&A, impact plans,
architecture briefs — all via the Responses API with streaming and reasoning effort.
gpt-5.3-codex powers generation: implementation-ready code with migrations and tests.
`text-embedding-3-small` powers hybrid semantic retrieval over graph nodes. Model
fallback chains (sol → terra → luna → 5.5) make the app resilient to account tiers.

**Engineering.** FastAPI backend; AST-based Python parsing (routes, models, calls,
router prefixes) + JS/TS heuristics + Markdown mention linking; deterministic
blast-radius propagation with per-edge weights and hop decay; SSE streaming; a
zero-dependency custom canvas force-graph renderer; 28 pytest tests; Docker; the
graph persists across restarts (JSON + optional Neo4j Aura mirror). ArchitectOS can
even ingest **its own codebase** — 283 nodes, 613 edges.

**Responsible by design.** Secrets are redacted before storage or any model call
(visible counter in the UI); repository content is treated as untrusted evidence,
never instructions (prompt-injection boundary); every `[[node]]` citation the model
makes is audited against the graph and unverifiable ones are struck through; code
generation is a preview behind an explicit "Approve plan" action — nothing is ever
applied to the repo.

**What's next.** Multi-repo graphs, PR-time impact checks in CI, Jira/Slack/Confluence
ingestion, and autonomous refactoring agents that plan against the graph before they
touch code.

---

## 3-minute video script

> Record at 1920×1080, dark room lighting = the UI pops. Have the app open with the
> demo repo loaded, Impact tab ready. Speak the **bold** lines; do the *italic* actions.

**[0:00–0:20] Hook — the goldfish problem**
**"Every AI coding assistant today has the same flaw: it sees your prompt, but not
your system. It'll happily write code that breaks three services it's never heard of.
This is ArchitectOS — an AI software architect with a persistent knowledge graph of
your entire codebase."**
*Screen: the graph galaxy, slowly panning. Point at clusters: auth, orders, payments,
frontend, docs.*

**[0:20–0:45] The graph**
**"ArchitectOS ingests any repo — here's an e-commerce platform — and builds a typed
knowledge graph: files, functions, API endpoints, database models, even the design
docs. And it's cross-stack: this frontend login page is linked to the exact backend
endpoint it calls, down to the User table behind it."**
*Action: search "login", click `login.js`, show the neighbor chips; click through to
`POST /api/auth/login`.*

**[0:45–1:30] The money shot — impact analysis**
**"Now the part every engineer does by hand before a big change. Let's ask: add OAuth
login with Google."**
*Action: Impact tab → click the suggestion → the blast radius ripples across the graph.*
**"The graph computes the blast radius deterministically — 110 nodes across 13
services. Red seeds, orange high-risk, and it knows *why* each one is affected — this
endpoint is exposed by the auth routes; this ADR documents them. Then GPT-5.6 turns
that radius into a real plan: schema changes, the account-takeover risk on unverified
emails, feature-flag rollout, test plan, and which docs to update."**
*Scroll the plan; click a citation chip to flash the node in the graph.*

**[1:30–2:15] Codex writes it**
**"One click hands the plan to Codex."**
*Action: "Generate the code with Codex →" — code streams in.*
**"gpt-5.3-codex writes the implementation grounded in the graph: the OAuth module
with signed state, the migration making password_hash nullable, the routes, the
frontend button, tests, and — my favorite — it amends the architecture decision
record, because the graph knows that doc exists."**
*Scroll: migration file, tests, ADR amendment, integration notes.*

**[2:15–2:40] Ask + architecture**
**"It's also the fastest onboarding tool you've ever used."**
*Action: Ask → "How does checkout work end to end?" — answer streams with citations.
Architect tab → brief with observations.*
**"Every claim cites a graph node. And the architecture brief finds real issues — like
our checkout charging cards outside the DB transaction."**

**[2:40–3:00] Close**
**"ArchitectOS: GPT-5.6 for reasoning, Codex for code, and a knowledge graph so they
finally know what they're changing. It ingests any repo — it even understands its own
codebase. Built in a week for OpenAI Build Week. Thanks for watching."**
*Screen: ingest ArchitectOS itself, graph rebuilds — cut to logo.*

---

## Judging criteria → what to emphasize

| Criterion | Our answer |
|---|---|
| Technical implementation | Typed cross-stack graph from AST parsing; deterministic explainable blast radius; Responses API streaming with model fallback chains; 28 tests; self-ingestion |
| Design & UX | Zero-dependency canvas graph with risk-colored ripple animation; streamed answers with clickable node citations; honest offline-demo labeling |
| Potential impact | Impact analysis + onboarding + grounded codegen = the daily pain of every team on a non-trivial codebase |
| Idea quality | Persistent org-level memory for AI engineering — assistants that know the system, not the snippet |

## Submission checklist

- [ ] Push the repo to GitHub (`Danishahmedkhan11/architectos`) — include a screenshot GIF in the README
- [ ] Record the 3-min video with **live API key** (real GPT-5.6/Codex streaming), audio narration
- [ ] Upload to YouTube as **Public**, no copyrighted music
- [ ] Devpost: description above + category + video link + repo link
- [ ] Test `./run.sh` on a clean machine (or `docker build`) before submitting
- [ ] Submit **before July 21, 5:00 pm PT**
