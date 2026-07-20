# 🏛️ ArchitectOS

**An AI software architect that actually knows your codebase — not just the text you paste into a chat window.**

Point it at any repository. It reads every file, builds a **knowledge graph** of how
the code actually connects (which file calls which endpoint, which endpoint touches
which database model, which doc describes which module), and then lets an LLM reason
*over that graph* instead of guessing from a single prompt.

---

## The problem

When you ask a typical AI coding assistant to make a change, it only looks at what
you just typed — not the rest of your actual project. It doesn't know your other
files, how they connect to each other, or decisions your team already made and wrote
down somewhere. So it often suggests code that *looks* right but quietly breaks
something else, because it never really understood the full picture. That's why
engineers still have to manually dig through a codebase themselves before making any
real change — nothing else has done that homework for them.

## The solution

ArchitectOS reads an entire codebase first and builds a map of how everything in it
actually connects — which files talk to which other files, which parts hit which
database, which documents explain which features, and so on. That map is called a
**knowledge graph**, and it's what the AI actually looks at before it answers a
question, plans a change, or writes any code — instead of just guessing from your
one message. In short: not an AI that only sees what you just typed, but one that
already understands how your whole project fits together.

## What it actually does

Four things, all grounded in the same graph:

1. **Ask** — ask a question about the codebase in plain English. The answer cites the
   real files/functions it used, and clicking a citation highlights that exact node
   on the graph.
2. **Impact analysis** — describe a change you want to make ("add rate limiting to
   the API"). The graph computes the **blast radius** — every file, endpoint, and doc
   that change would touch — deterministically, by walking real edges, not by asking
   an LLM to guess. The model then turns that into a written implementation plan.
3. **Generate** — one click from an impact plan, and the model writes the actual
   implementation code for that change. This is a **preview only** — nothing is ever
   written back to your repository.
4. **Architecture brief** — a one-click summary of the whole codebase: modules, key
   request flows, the data model, and where the coupling hot-spots are.

## How it works, step by step

```
1. You paste a local folder path or a GitHub URL and click Ingest
        │
        ▼
2. The backend clones/reads the repo and parses every file
   (Python via the `ast` module, JS/TS and Markdown via lightweight heuristics)
        │
        ▼
3. It builds a graph: nodes = files, functions, classes, API endpoints,
   database models, docs — edges = imports, calls, "exposes this endpoint",
   "uses this model", "documents this file", etc.
        │
        ▼
4. That graph is what you see on screen, and it's what every feature below
   reads from — search, node clicks, Ask, Impact, Generate, Architecture
```

Nothing here needs a model to work — steps 1–4 are 100% deterministic and free. The
model only gets involved once you actually ask a question, request an impact plan,
or generate code; even then, it's given real retrieved graph context to work from
instead of just your raw prompt.

## Quickstart

```bash
git clone <your-repo-url>
cd architectos
cp .env.example .env    # see "Which model should I use?" below before you fill this in
./run.sh
# → http://127.0.0.1:8321
```

`run.sh` creates a Python virtual environment and installs dependencies on first run,
then starts the app. Everything — the API and the frontend — is served from that one
URL; there's no separate frontend build step.

Once it's open: paste a local path (e.g. `/Users/you/some-project`) or a public GitHub
URL (e.g. `https://github.com/pallets/flask`) into the box at the top and click
**Ingest**. Give it a few seconds for larger repos, then explore.

## Which model should I use?

This app needs an LLM only for the "reasoning" parts (Ask / Impact / Generate /
Architecture) — search, the graph itself, and blast-radius are always free and local.
You have two real options:

| | Cost | Setup | Quality |
|---|---|---|---|
| **OpenRouter — free (default here)** | $0 | `OPENROUTER_API_KEY` from [openrouter.ai](https://openrouter.ai) (no payment needed to sign up), set both model vars to `openai/gpt-oss-20b:free` | Good enough to see every feature work end-to-end. Can be rate-limited / occasionally returns a "not enough quota" error under OpenRouter's free tier — that's expected, not a bug. |
| **Native OpenAI — GPT-5.6 + Codex** | Real API cost | A native `OPENAI_API_KEY` from [platform.openai.com](https://platform.openai.com) | Full-strength reasoning and code generation, no rate limits tied to a free quota. Only needed if you specifically want GPT-5.6/Codex. |

Your `.env` needs exactly one of `OPENAI_API_KEY` or `OPENROUTER_API_KEY` set — if
both are set, OpenRouter wins. See `.env.example` for the exact variables and comments.

**No key at all?** The app still runs. The graph, search, and blast-radius analysis
are fully live regardless. Ask/Impact/Generate will show an honest message asking you
to add a key, instead of pretending to answer.

## Using each feature

| Feature | Try this |
|---|---|
| Ingest | Paste a GitHub URL (a small public repo works best for a first try) → **Ingest** |
| Search / explore | Type in the search box, or click any node on the graph to see its source and neighbors |
| Ask | *"What does \<some file you saw on the graph\> do?"* — reusing an exact file/function name works best |
| Impact | *"Add logging"* or *"Add a new API endpoint"* — then watch the blast radius light up on the graph |
| Generate | Click **"Approve plan → Codegen preview"** at the bottom of an Impact result |
| Architecture | Just click **"Analyze architecture"** — no input needed |

## Configuration reference

All of this lives in `.env` (copy `.env.example` to start):

| Variable | What it does |
|---|---|
| `OPENAI_API_KEY` | Native OpenAI key. Enables real embeddings-based retrieval (better search quality) in addition to reasoning. |
| `OPENROUTER_API_KEY` | Alternative provider; wins over `OPENAI_API_KEY` if both are set. Good for free/cheap testing, but embeddings don't work through OpenRouter — search falls back to keyword matching. |
| `ARCHITECTOS_REASONING_MODEL` | Model used for Ask / Impact / Architecture. Falls back automatically through a chain of alternatives if the first choice isn't available to your account. |
| `ARCHITECTOS_CODEX_MODEL` | Model used for the Generate/codegen step. |
| `ARCHITECTOS_EMBED_MODEL` | Embedding model (native OpenAI only). |
| `ARCHITECTOS_DEMO` | `auto` (default) = live if a key is present; `on` = force the offline fallback message even with a key. |
| `ARCHITECTOS_HOST` / `ARCHITECTOS_PORT` | Where the server listens. Default `127.0.0.1:8321`. |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | Optional. Mirrors the graph to a Neo4j Aura database so it survives across machines, not just restarts. Leave blank to just use the local `data/graph.json` file — simpler, and the right choice for most people. See the Neo4j note below if you do use this. |

## Project structure

```
architectos/
├── backend/          FastAPI app — everything server-side
│   ├── main.py           API routes, app startup
│   ├── ingest.py          walks a repo and builds the graph
│   ├── parsers.py         per-language file parsing (Python AST, JS/TS, Markdown)
│   ├── kg.py               the knowledge graph data structure itself
│   ├── retrieval.py        finds relevant graph nodes for a question
│   ├── impact.py           blast-radius computation
│   ├── llm.py               model calls, fallback chains, offline-mode handling
│   └── config.py            reads .env into typed settings
├── static/           The frontend — plain HTML/CSS/JS, no build step, no framework
│   ├── index.html         page structure
│   ├── app.js               all UI logic and API calls
│   ├── graph.js             the canvas graph renderer
│   └── styles.css
├── tests/            pytest suite (see below)
├── data/             created at runtime — local graph cache + cloned repos (gitignored)
├── run.sh            one-command launch (creates the venv on first run)
├── requirements.txt
├── Dockerfile
└── .env.example
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

18 tests covering: file parsing, the graph data structure, retrieval, blast-radius
computation, and the full API surface including streaming responses. They run against
a small synthetic fixture repo generated on the fly — no external network access or
API keys needed, so they'll pass the same way on your machine as they do here.

## Docker

```bash
docker build -t architectos . && docker run -p 8321:8321 --env-file .env architectos
```

## Troubleshooting

A few things that look like bugs but aren't, in case you hit them:

- **"Unknown job" error while ingesting a GitHub URL.** The clone was still running
  when the server process restarted (most likely because you're running with
  `--reload` and saved a file mid-clone). Just click **Ingest** again. For anything
  you're recording or demoing, run without `--reload`.
- **Old data shows up after you thought you cleared everything.** If you've configured
  Neo4j, the app checks Neo4j *before* the local `graph.json` on startup — so a graph
  persisted there earlier will keep reappearing until you either ingest something new
  (which fully overwrites it) or clear it manually from the Neo4j console. If you're
  not intentionally using Neo4j for cross-machine persistence, it's simplest to just
  leave those four `NEO4J_*` variables blank.
- **You edited `static/app.js` or `graph.js` but the browser still runs the old
  version.** Some browsers cache these aggressively even though the server sends
  `Cache-Control: no-cache`. Hard-refresh, or bump the version query string in
  `static/index.html`'s two `<script>` tags (`graph.js?v=2` → `?v=3`, etc.).
- **A free-tier model (like `gpt-oss-20b:free`) returns a 402 / "insufficient
  credit" error.** OpenRouter's free models still draw against a small quota tied to
  your account balance — it isn't truly unlimited. The app automatically falls back to
  an honest "couldn't get a live answer" message rather than failing silently; the
  graph, search, and blast-radius results above it are unaffected either way.

## Planning

Before implementation started, the overall design — the knowledge-graph model, the
node/edge schema, and the feature set — was planned out with GPT-5.6, producing a
27-page design specification: [`docs/ArchitectOS_Design_Specification.pdf`](docs/ArchitectOS_Design_Specification.pdf).
The shipped implementation deliberately follows that spec's "Must have" priorities
with lighter infrastructure in places, rather than a 1:1 build-out — this is a note
on the *planning* phase specifically, separate from the Codex code-generation work
described below.

## Built with Codex

> ⚠️ **TODO before submitting** — this section needs to be filled in with what
> actually happened in a real Codex CLI session, and the `/feedback` Codex
> Session ID needs to be added below. Don't submit with this placeholder still
> in place — an inaccurate claim here risks disqualification under Build
> Week's rules.
>
> To do this properly:
> 1. Install and run the real Codex CLI ([official docs](https://developers.openai.com/codex/cli); `npm install -g @openai/codex`, then `codex auth`, then `codex` inside this repo).
> 2. Have it do genuine, meaningful core-functionality work on this codebase — not a token edit. A good candidate: extend the Generate/codegen flow, or add a feature to the graph ingestion.
> 3. Note below exactly what existed *before* that session vs. what Codex added *during* it (Build Week's rules require this distinction for pre-existing projects).
> 4. Run `/feedback` in that Codex session to get the session ID, and paste it here.

**What existed before this Codex session:** _(fill in)_

**What Codex added during this session:** _(fill in)_

**Where Codex accelerated the workflow:** _(fill in — e.g. specific files, specific time saved)_

**Codex Session ID:** _(fill in)_

## Roadmap

Multi-repo graphs · CI/CD pull-request impact checks · Jira/Slack/Confluence ingestion
· live drift monitoring · autonomous refactoring agents.
