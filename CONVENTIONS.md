# DocFlow Documentation Conventions

This file defines the strict structure and formatting standards required for all documentation managed by DocFlow. Any AI coding agent creating or updating documentation MUST adhere to these rules.

---

## 1. Architecture Model (1 App Repo : 1 Doc Repo)

DocFlow strictly enforces a **1-to-1 pairing** between an application source repository (or monorepo root) and its dedicated documentation repository:
- One documentation repository serves exactly **one** application repository.
- If the target is a **monorepo**, DocFlow treats the monorepo root as the single application entity, and top-level packages (`packages/`, `apps/`, `services/`) are chunked as feature directories under `features/`.

---

## 2. Agent Execution Modes & Output File Location Rules

DocFlow supports two primary agent execution modes:

1. **Shell Mode (`mode: shell`)**:
   - DocFlow automatically executes the configured CLI coding agent (e.g., `agy`, `opencode`, `claude`) via subprocess.
   - Commands MUST include direct workspace access to the target documentation repository (e.g., `agy --dangerously-skip-permissions --add-dir {docs_repo} -p "Follow every instruction in {prompt_file}."`).
   - The coding agent writes documentation files directly into the target paths (`<type>/index.md` or `features/<feature>/index.md`, plus `context.json`, `files.md`, `changelog.md`), and DocFlow automatically moves processed prompts from `prompts/pending/` to `prompts/completed/`.

2. **Manual Mode (`mode: manual`)**:
   - DocFlow generates caveman prompt markdown files staged inside `prompts/pending/`.
   - Every generated prompt includes a **`⚠️ CRITICAL OUTPUT FILE LOCATION DIRECTIVE`** specifying the exact absolute file paths in the target documentation repository.
   - When an AI agent executes a pending prompt, it MUST write directly to the target absolute file paths in the documentation repository rather than storing output into internal session artifact storage (`.gemini` or scratch space).

---

## 3. Type and feature directory layout

Doc types are folders you configure (`architecture`, `features`, `front-end`, …). Each type stores the same four files.

`features` is the only type that is split per module:

```
features/<feature-name>/
├── index.md
├── context.json
├── files.md
└── changelog.md
```

Other types use a single folder named after the type (`front-end/index.md`, …).

---

## 2. Human Documentation Standard (`index.md`)

`index.md` MUST start with a YAML frontmatter block and adhere to GitHub Flavored Markdown (GFM) standards.

### Required Frontmatter
```yaml
---
title: "Feature Title"
description: "A concise 1-2 sentence description of the feature."
type: "feature"          # any configured kind: architecture | features | api | front-end | …
status: "stable"         # stable | wip | deprecated
last_updated: "YYYY-MM-DDTHH:MM:SSZ"
source_repo: "repo-name"
related_features:
  - "related-feature-1"
tags: ["tag1", "tag2"]
---
```

### Document Section Structure
1. **Title & Summary Blockquote**:
   ```markdown
   # Feature Title

   > High-level summary of purpose and functionality.
   ```
2. **Overview**: 2-3 paragraphs detailing workflow, business logic, and implementation details.
3. **Architecture**: Overview of internal architecture and diagrams (using `mermaid` code blocks where appropriate). Use GFM callouts (`> [!NOTE]`, `> [!IMPORTANT]`, `> [!WARNING]`).
4. **Key Files**: Table listing core files and their specific responsibilities.
5. **API Surface**: Description of public functions, endpoints, types, or interfaces.
6. **Dependencies**: Internal feature dependencies and external packages.
7. **Change History**: Reverse chronological table of changes.

---

## 3. LLM-Optimized Format (`context.json`)

`context.json` provides an ultra-compact, high-density machine-readable representation of the feature to minimize token consumption during context loading.

### Required Schema
```json
{
  "feature": "feature-name",
  "summary": "Concise overview of functionality",
  "status": "stable",
  "last_updated": "YYYY-MM-DDTHH:MM:SSZ",
  "key_files": [
    {
      "path": "relative/path/to/file.ext",
      "role": "Brief description of responsibility",
      "lines": 120
    }
  ],
  "public_api": [
    {
      "name": "function_or_endpoint_name",
      "returns": "ReturnType",
      "file": "relative/path/to/file.ext"
    }
  ],
  "dependencies": {
    "internal": ["feature-a"],
    "external": ["package-x>=1.0.0"]
  },
  "patterns": ["pattern-name-or-convention"],
  "related": ["related-feature-1"],
  "entry_point": "relative/path/to/entry.ext"
}
```

---

## 4. Generate watermark (`.docflow-state.json`)

DocFlow records the last documented application commit in `.docflow-state.json` in the docs repo. That file is machine state, not human documentation. Agents must not treat it as a content target. Subsequent updates cover only commits after that SHA unless the user requests last-N or a full regeneration.

Imported files must not be overwritten on import. Updates to existing `index.md` / `context.json` happen through generate, which receives the current files and applies the new git range.

---

## 5. Framework-aware documentation

When DocFlow detects a framework (Laravel first), it skips dependency and generated directories (`vendor/`, `storage/`, framework caches) and avoids creating feature modules for framework scaffolding (`bootstrap/`, `public/`).

Agents must **document the application layered on the framework**, not the framework itself:
- Do not write Laravel/Filament/Inertia/Vue/React tutorials or explain bootstrap, the container, or vendor packages.
- Do document this project's models, controllers, policies, jobs, routes, migrations, Filament resources, Inertia pages, and Vue/React components.

During init, DocFlow may write `.docflow-stack.json` in the docs repo. When present, all documentation agents must follow its `guidance`, `skip_paths`, and `document` fields.
