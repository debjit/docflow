# DocFlow

Agent-powered documentation from git activity. DocFlow writes dual-audience docs (Markdown for humans, JSON for LLMs) by generating focused prompts any coding agent can run — Antigravity, Cursor, Claude Code, Cline, and others. No vendor LLM API key.

## Quickstart

```bash
git clone https://github.com/debjit/docflow.git
cd docflow
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

From your application repo (the docs folder must be empty the first time):

```bash
docflow init
docflow pull
docflow generate
```

| Command | What it does |
| --- | --- |
| `docflow init` | Pair app + docs. You define types (`front-end: React UI docs`). Defaults: `architecture`, `features`. |
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
├── CONVENTIONS.md
├── .docflow.yml
├── .docflow-state.json          # last documented SHA (not human docs)
├── llms.txt
├── llms-full.txt
├── architecture/                # example type
├── features/<module>/           # only if you keep a features type
│   ├── index.md
│   ├── context.json
│   ├── files.md
│   └── changelog.md
├── front-end/                   # example custom type
└── prompts/
    ├── pending/
    └── completed/
```

`features` is split per module. Other types are a single folder named after the type.

## Configuration

Project config lives only in the **docs** repo (`.docflow.yml`). DocFlow does not write into the application source tree. `docflow projects` lists and switches docs projects from a user index (`$XDG_CONFIG_HOME/docflow/projects.yml`).

`.docflow.yml` in the docs repo:

```yaml
project:
  name: "MyApplication"

app:
  repo_path: "/path/to/app-repo"

docs:
  repo_path: "/path/to/docs-repo"
  types:
    - name: architecture
      description: System layout, hosting, and shared packages
    - name: features
      description: Feature and module documentation scanned from the codebase
    - name: front-end
      description: React UI docs

agent:
  mode: "shell"   # or "manual"
  command: 'agy --dangerously-skip-permissions --add-dir {docs_repo} -p "Follow every instruction in {prompt_file}."'

platform:
  type: "github"  # github | gitlab | bitbucket | generic
  auto_mr: true

generation:
  full_diff_threshold: 200
  ignore:
    - "*.lock"
    - "node_modules/"
    - "dist/"
    - "__pycache__/"
```

- **shell**: DocFlow runs your CLI agent; prompts move from `prompts/pending/` to `prompts/completed/`.
- **manual**: Prompts stay in `prompts/pending/` with exact output paths.

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
