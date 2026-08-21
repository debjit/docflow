# DocFlow

Agent-powered documentation from git activity. DocFlow writes dual-audience docs (Markdown for humans, JSON for LLMs) by generating focused prompts any coding agent can run — Antigravity, Cursor, Claude Code, Cline, and others. No vendor LLM API key.

## Platform support (v1)

Version 1 targets **Linux** (macOS generally works too). On Windows, install [WSL](https://learn.microsoft.com/en-us/windows/wsl/install), clone inside your WSL distro, and follow the Linux steps below. Native PowerShell/cmd is not supported in v1.

## Quickstart

Install once (from the DocFlow source tree):

### Linux / macOS / WSL

```bash
git clone https://github.com/debjit/docflow.git
cd docflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Every later session, activate the venv then start the UI. DocFlow opens the last docs project.

```bash
source .venv/bin/activate
docflow ui
```

Same venv, other entry points:

```bash
source .venv/bin/activate
docflow              # interactive menu
docflow generate     # update docs from new commits
docflow pull
```

If the `docflow` command is not found, the venv is not active — run `source .venv/bin/activate` again.

First-time pairing (docs folder must be empty):

```bash
docflow init
```

After that, **Update docs** in the UI reuses the last agent and model. Change them only when you want a different LLM.

| Command | What it does |
| --- | --- |
| `docflow init` | Pair app + docs. Defaults: `architecture`, `database`, `models`, `functions`, `routes`, `pages`. |
| `docflow pull` | `git pull` on the app repo, then list commits not yet documented. |
| `docflow generate` | Update existing docs from **new commits only**. |
| `docflow import --from PATH --type NAME` | Copy files into a type folder. Never overwrites. |
| `docflow generate --full` | Rebuild docs. Init cannot be run twice. |
| `docflow ui` | Full-screen UI. |
| `docflow projects` | List / open / add / remove docs projects. |
| `docflow` | Interactive menu (TTY). |

## Features

- One application repo paired with one docs repo
- User-defined doc types (folders). Import later without overwriting
- Remembers the last documented commit so generate does not repeat the same range
- `index.md` + `context.json` per section
- MCP server (`stdio` / `sse`)
- Publish a docs branch and open a PR/MR (GitHub, GitLab, Bitbucket)

## Workflow

```mermaid
flowchart TD
  A[App repo] -->|optional git pull| B[New commits since last update]
  B --> C[Prompt builder]
  C --> D[Agent: shell or manual]
  D --> E[Docs repo]
  E --> F[Publish PR]
  E --> G[MCP server]
```

## Docs repo layout

```text
docs-repo/
├── architecture/                # one overview
├── database/<unit>/
├── models/<unit>/
├── functions/<unit>/
├── routes/<unit>/
├── pages/<unit>/
├── llms.txt
├── llms-full.txt
└── .docflow/                    # machinery, not docs
    ├── config.yml
    ├── state.json
    ├── stack.json
    ├── CONVENTIONS.md
    └── prompts/
        ├── pending/
        └── completed/
```

Application docs live in type folders. Config, prompts, and generate state live in `.docflow/`.

## Configuration

Project config lives only in the **docs** repo (`.docflow/config.yml`). DocFlow does not write into the application source tree. `docflow projects` lists and switches docs projects from a user index (`$XDG_CONFIG_HOME/docflow/projects.yml`).

`.docflow/config.yml` in the docs repo:

```yaml
project:
  name: "MyApplication"

app:
  repo_path: "/path/to/app-repo"
  branch: "main"   # tracked branch; change later if master was renamed to main, or develop becomes master

docs:
  repo_path: "/path/to/docs-repo"
  types:
    - name: architecture
      description: System layout, hosting, and packages this app uses
    - name: database
      description: Schema and migrations
    - name: models
      description: Domain models
    - name: functions
      description: Application services, jobs, actions, and controllers
    - name: routes
      description: HTTP routes
    - name: pages
      description: UI pages and indexes

agent:
  mode: "shell"   # or "manual"
  name: "cursor-agent"   # last agent used; next run defaults to this
  plan_model: "composer-2.5"       # search / structure (init stack survey)
  model: "composer-2.5-fast"       # writes each section
  command: 'agent --workspace {docs_repo} --force --trust -p "Follow every instruction in {prompt_file}."'

platform:
  type: "github"  # github | gitlab | bitbucket | generic
  auto_mr: true

generation:
  concurrency: 1   # parallel agent jobs; use 1 unless the machine can run several LLMs
  full_diff_threshold: 200
  framework: auto   # auto | none | laravel
  ignore:
    - "*.lock"
    - "node_modules/"
    - "dist/"
    - "__pycache__/"
```

- **`app.branch`**: application branch DocFlow documents (`main`, `master`, `develop`, …). Set at init; change it on Update docs. New-commit updates and the dashboard use this branch, not whatever is checked out. Switching branch also scans for new units that were not documented yet.
- **`agent.name` / `agent.plan_model` / `agent.model`**: last coding agent, plus two LLMs. The **plan** model runs the init stack survey (search the app and structure the docs list). The **work** model writes each section. Init and generate save these; the next UI/CLI run pre-selects them. Cursor defaults: plan `composer-2.5`, work `composer-2.5-fast`.
- **`generation.concurrency`**: how many agent jobs run at once. Default is **1**. Raise it only if the PC can run several coding agents together. `--jobs N` or `DOCFLOW_JOBS` overrides for one run.
- **`generation.ignore`**: merged with DocFlow defaults and framework profiles during scan and diff.
- **`generation.features`**: units selected during init. Later `generate` only updates those sections.
- **Section picker**: after the agent inspects composer/packages and app structure, init lists application units (models, routes, pages, …). CLI glue such as `main`/`menu` is not listed. `--yes` skips the picker (CI).
- **`.docflow/stack.json`**: written during init by a stack survey agent job; later prompts use its `guidance` to focus on application code, not framework internals.

- **shell**: DocFlow runs your CLI agent; prompts move from `.docflow/prompts/pending/` to `.docflow/prompts/completed/`.
- **manual**: Prompts stay in `.docflow/prompts/pending/` with exact output paths.

`docflow status` (alias `docflow info`) shows types, last documented commit, and new commits. It does not write `status/wip.md`.

## CI

```bash
docflow init --repo /path/to/app-repo --docs /path/to/docs-repo --agent manual --fresh
docflow generate --repo /path/to/app-repo --docs /path/to/docs-repo
docflow pull --repo /path/to/app-repo --docs /path/to/docs-repo
docflow import --docs /path/to/docs-repo --from /path/to/files --type front-end
docflow status --repo /path/to/app-repo --docs /path/to/docs-repo
docflow publish --docs /path/to/docs-repo --platform github
docflow serve --docs /path/to/docs-repo --transport stdio
```

If `pip` is missing after `venv`:

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python3
.venv/bin/pip install -e .
```

## Tests

```bash
pytest -v
```

## License

[MIT](LICENSE)
