# Repograte

<p align="left">
  <img src="https://img.shields.io/badge/python-3.12%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python Version" />
  <img src="https://img.shields.io/badge/Engine-LangGraph-8A2BE2?style=for-the-badge" alt="Built with LangGraph" />
  <img src="https://img.shields.io/badge/Sandbox-E2B%20MicroVM-ff6b6b?style=for-the-badge" alt="Sandbox" />
  <img src="https://img.shields.io/badge/maintained%20with-uv-06b6d4?style=for-the-badge" alt="Package Manager" />
</p>

An autonomous migration agent that takes a single file, plans a refactor, writes it, verifies it in a real sandboxed environment, self-corrects on failure, and stops for a human before anything gets pushed.

Scoped MVP: **migrating React class components to functional components with hooks.**

The architecture underneath is not specific to that migration - swapping the Architect/Engineer prompts and the sandbox's verification command targets a different mechanical migration entirely. It also isn't limited to TypeScript: the parser and sandbox both work against plain `.js`/`.jsx` too (see [Configuration reference](#%EF%B8%8F-configuration-reference)).

---

## 🚀 How it works

```mermaid
graph TD;
	__start__([__start__]):::startEnd
	architect(Architect Agent):::agent
	engineer(Engineer Agent):::agent
	qa_validator(QA Validator):::qa
	sandbox(Isolated Sandbox Run):::infra
	debugger(Log Debugger):::agent
	human_review(Human Review Interrupt):::human
	publish_pr(Publish PR):::infra
	__end__([__end__]):::last

	__start__ --> architect;
	architect --> engineer;
	debugger --> engineer;
	engineer --> qa_validator;
	human_review -.-> engineer;
	human_review -.-> publish_pr;
	qa_validator -.-> engineer;
	qa_validator -.-> sandbox;
	qa_validator -.-> human_review;
	sandbox -.-> debugger;
	sandbox -.-> human_review;
	publish_pr --> __end__;

	%% Premium Neon / Developer Palette
	classDef default fill:#0f172a,stroke:#38bdf8,color:#f8fafc,stroke-width:2px;
	classDef agent fill:#1e1b4b,stroke:#818cf8,color:#e0e7ff,stroke-width:2px;
	classDef qa fill:#311042,stroke:#d946ef,color:#fdf4ff,stroke-width:2px;
	classDef infra fill:#022c22,stroke:#34d399,color:#ecfdf5,stroke-width:2px;
	classDef human fill:#451a03,stroke:#fb923c,color:#fff7ed,stroke-width:2px;
	classDef startEnd fill:#1e293b,stroke:#94a3b8,color:#cbd5e1,stroke-width:2px;
	classDef last fill:#1e293b,stroke:#94a3b8,color:#cbd5e1,stroke-width:2px;
```

| Node             | Model                      | Job                                                                                                                                                                                                                                       |
| ---------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **architect**    | Claude                     | Reads the file, writes a migration plan. Best-effort, feature-flagged: also clones the repo, finds sibling components already written in it, and hands the Architect a couple of relevant examples for consistency (`ENABLE_RAG_CONTEXT`) |
| **engineer**     | Claude (structured output) | Turns the plan (or the last failure's feedback) into `search_block` / `replace_block` diffs - never rewrites the whole file                                                                                                               |
| **qa_validator** | DeepSeek + plain code      | A deterministic check confirms every `search_block` exists verbatim and none overlap (no LLM call needed for that part); DeepSeek then judges only whether the replacement is logically sound                                             |
| **sandbox**      | E2B microVM                | Clones the real repo, applies the diff, runs the configured install/test commands (default: `npm install` + `tsc --noEmit`, override via env vars or `--install-cmd`/`--test-cmd`)                                                        |
| **debugger**     | Claude                     | Condenses raw sandbox failure logs into a focused instruction for the next `engineer` attempt, so retries don't re-forward megabytes of npm/tsc output                                                                                    |
| **human_review** | —                          | Pauses the graph for real (`interrupt()`) and waits for a person to approve, reject-with-feedback, or let a WIP through. Sees the Architect's full plan, not just the Engineer's one-line reasoning                                       |
| **publish_pr**   | —                          | Branches, commits, pushes, opens the PR (draft + `[WIP]` + failure summary if the loop never converged)                                                                                                                                   |

If `sandbox` **or** `qa_validator` keeps failing, the run loops (`engineer` → `qa_validator`
→ `sandbox` → `debugger` → `engineer` ...) until it either succeeds or hits
`MAX_CORRECTION_LOOPS` (default **4**) - whichever node it fails at. At that point it
stops retrying and opens a **draft PR** with a markdown summary of every attempt
instead of trying forever.

## 📦 Project layout

```
repograte/
├── .env.example
├── .github/workflows/tests.yml   # CI: uv sync + pytest on push/PR
├── .gitignore
├── requirements.txt
├── pyproject.toml                 # deps, build-system, pytest config
├── tests/                         # pytest - pure logic + a mocked-external integration test
└── src/
    ├── run_cli.py                 # CLI entry point with the human-approval loop
    └── repograte/
        ├── config.py              # centralized env config (pydantic-settings)
        ├── ingestion/             # AST parsing & vector indexing
        │   ├── ast_parser.py      #   tree-sitter parser (.tsx/.jsx/.ts/.js) -> ASTComponent/ASTMethod
        │   ├── vector_store.py    #   Qdrant + FastEmbed indexer
        │   └── context.py         #   clones the repo, indexes sibling components, retrieves matches for the Architect
        ├── orchestration/         # The LangGraph state machine
        │   ├── schemas.py         #   Pydantic I/O contracts (diffs, QA results)
        │   ├── state.py           #   the shared graph state (TypedDict)
        │   ├── diffing.py         #   applies search/replace blocks (pure, unit-tested)
        │   ├── routing.py         #   conditional-edge logic (pure, unit-tested)
        │   ├── nodes.py           #   Architect/Engineer/QA/Sandbox/Debugger/Review/Publish
        │   └── graph.py           #   wires the nodes + routing + checkpointer together
        ├── sandbox/               # Isolated execution
        │   └── e2b_runner.py      #   boots a microVM, clones the repo, runs verification
        └── vcs/                   # Version control
            └── github_utils.py    #   branch, commit, push, open PR
```

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (or plain `pip` + `requirements.txt`)
- API keys: **Anthropic**, **DeepSeek**, **E2B**. **GitHub** token only if you want it to
  actually push branches and open PRs - without one, `create_pull_request` raises
  immediately, before touching git, rather than getting partway through a clone/commit
  and failing on push.

## 🛠️ Setup

```bash
git clone <this-repo> && cd Repograte

uv venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

uv sync                          # or: uv pip install -r requirements.txt

cp .env.example .env
# then fill in ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, E2B_API_KEY, GITHUB_TOKEN
```

Prefer not to activate the venv by hand? `uv run` works too, e.g.
`uv run python src/run_cli.py --help`.

## 💻 Running it

```bash
cd src
python run_cli.py \
  --repo-url https://github.com/your-org/your-repo.git \
  --file src/components/UserProfile.tsx \
  --code-file ./local-copy/UserProfile.tsx \
  --branch main
```

For a non-TypeScript repo, override the verification command for this run only:

```bash
python run_cli.py \
  --repo-url https://github.com/your-org/your-repo.git \
  --file src/components/UserProfile.jsx \
  --code-file ./local-copy/UserProfile.jsx \
  --test-cmd "npm run lint"
```

The graph runs architect → engineer → qa → sandbox on its own. As soon as it reaches
`human_review`, it pauses for real (this is a genuine LangGraph `interrupt()`, not a
sleep loop) and the CLI prints the plan and diff:

```diff
=== Human review requested ===
File:   src/components/UserProfile.tsx
Status: success  (loop 1)

Architect plan:
1. Replace the class declaration with a function component.
2. Convert this.state to a useState hook...

Reasoning:
Convert the class component to a function component using useState for local state...

Proposed changes:
--- search ---
class UserProfile extends React.Component {
--- replace ---
function UserProfile(props) {

Approve and open PR? [y/n]:
```

Answer `y` to open the PR, or `n` to give feedback and let it try again. Each run is
keyed by `f"{repo_url}:{file_path}"` as the checkpoint thread ID. By default
(`CHECKPOINT_PATH` set) this is backed by a local SQLite file, so **re-running the
exact same command later genuinely resumes an interrupted run** - including if you
killed the CLI process entirely - instead of silently starting over. Set
`CHECKPOINT_PATH=` (empty) to fall back to a pure in-memory checkpointer instead.

## 🧪 Testing

```bash
uv sync --group dev
uv run pytest -v
```

The suite covers the pure orchestration logic (diff application, routing, the loop-cap
fix), the AST parser, GitHub URL/PR-body helpers, and a full graph run - Claude,
DeepSeek, E2B, and GitHub all replaced with fakes - including a regression test for
cross-process resume. It does **not** exercise the live LLM/sandbox/GitHub calls or the
FastEmbed embedding step (the latter needs to download a model from Hugging Face on
first use - set `ENABLE_RAG_CONTEXT=false` if that egress isn't available to you, e.g.
in a locked-down CI runner).

## ⚙️ Configuration reference

Everything is read centrally in `config.py` from environment variables (or a `.env`
file at the repo root):

<details>
<summary><b>Click to expand full Environment Variable matrix</b></summary>

| Variable               | Default                            | Notes                                                                                                                     |
| ---------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `ANTHROPIC_API_KEY`    | —                                  | Architect / Engineer / Debugger                                                                                           |
| `ANTHROPIC_MODEL`      | `claude-sonnet-5`                  | Must be a real model id                                                                                                   |
| `DEEPSEEK_API_KEY`     | —                                  | QA validation                                                                                                             |
| `DEEPSEEK_BASE_URL`    | `https://api.deepseek.com/v1`      |                                                                                                                           |
| `E2B_API_KEY`          | —                                  | Sandbox execution                                                                                                         |
| `E2B_TEMPLATE`         | `base`                             | Build a custom template (`e2b template build`) with your toolchain preinstalled to cut ~20-30s off every verification run |
| `E2B_TIMEOUT_SECONDS`  | `300`                              |                                                                                                                           |
| `SANDBOX_INSTALL_CMD`  | `npm install --no-audit --no-fund` | Per-run override: `--install-cmd`                                                                                         |
| `SANDBOX_TEST_CMD`     | `npx tsc --noEmit`                 | Per-run override: `--test-cmd`. Use this for JS-only / non-tsc projects                                                   |
| `GITHUB_TOKEN`         | —                                  | Needs push + PR permissions (classic PAT: `repo` scope)                                                                   |
| `QDRANT_URL`           | —                                  | Leave blank for a throwaway local in-memory instance                                                                      |
| `QDRANT_API_KEY`       | —                                  | Only needed for Qdrant Cloud                                                                                              |
| `ENABLE_RAG_CONTEXT`   | `true`                             | Set `false` to skip the sibling-component retrieval step entirely (offline/CI use)                                        |
| `RAG_MAX_FILES`        | `40`                               | Cap on how many files the context-retrieval step will parse per run                                                       |
| `MAX_CORRECTION_LOOPS` | `4`                                | Applies to both the QA-validation loop and the sandbox-verification loop                                                  |
| `CHECKPOINT_PATH`      | `.repograte/checkpoints.sqlite`    | Set to an empty string for a pure in-memory checkpointer (no cross-process resume)                                        |

</details>

## ⚠️ Known limitations

- The RAG context step clones the repo independently of `sandbox`/`publish_pr` (each
  stage clones its own working copy); fine for an MVP, but redundant for large repos.
- Against a persistent Qdrant Cloud collection, the context step doesn't dedupe
  re-indexed files across runs of the same repo - each run adds new points.
