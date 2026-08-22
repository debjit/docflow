"""
Export DocFlow documentation to static-site generator content folders.

Pure file processing — no agents, no LLM calls, no network. Reads the human
`index.md` pages from a docs repo and writes a converted markdown tree for a
target documentation app (Docusaurus first, more writers later). Machine files
(`context.json`, `files.md`, `changelog.md`) and `.docflow/` state are never
exported, and the docs repo itself is never modified.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from docflow.core.workspace import SPLIT_DOC_TYPES

SUPPORTED_FORMATS = ("docusaurus",)

_SKIP_ENTRIES = {".git", ".docflow"}


@dataclass
class UnitDoc:
    """One human documentation page (`<type>/index.md` or `<type>/<unit>/index.md`)."""

    doc_type: str
    name: str
    source_path: str
    frontmatter: Dict = field(default_factory=dict)
    body: str = ""


@dataclass
class SiteExportResult:
    out_dir: str
    fmt: str
    pages: int = 0
    files: List[str] = field(default_factory=list)


def validate_out_path(docs_repo_path: str, out_dir: str) -> None:
    """Refuse output locations inside the docs repo (it must stay pure markdown)."""
    docs_abs = os.path.abspath(docs_repo_path)
    out_abs = os.path.abspath(out_dir or "")
    if out_abs == docs_abs:
        raise ValueError(
            f"Output directory '{out_dir}' is the docs repo itself. "
            "Choose a folder outside it."
        )
    rel = os.path.relpath(out_abs, docs_abs)
    if not rel.startswith(os.pardir):
        raise ValueError(
            f"Output directory '{out_dir}' is inside the docs repo ('{rel}'). "
            "The docs repo must stay browsable markdown; export elsewhere."
        )


def parse_frontmatter(text: str) -> Tuple[Dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    rest = text[end + 4 :].lstrip("\n")
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        data = None
    return (data if isinstance(data, dict) else {}), rest


_CALLOUT_KINDS = {
    "NOTE": "note",
    "TIP": "tip",
    "IMPORTANT": "info",
    "WARNING": "warning",
    "CAUTION": "caution",
    "DANGER": "danger",
}
_GITHUB_CALLOUT_RE = re.compile(
    r"^>\s*\[!(" + "|".join(_CALLOUT_KINDS) + r")\]\s*(.*)$",
    re.IGNORECASE,
)


def convert_github_callouts(body: str) -> str:
    """Rewrite `> [!NOTE]` GitHub callouts into Docusaurus admonition blocks."""
    lines = body.splitlines()
    out: List[str] = []
    i = 0
    while i < len(lines):
        match = _GITHUB_CALLOUT_RE.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        kind = _CALLOUT_KINDS[match.group(1).upper()]
        title = (match.group(2) or "").strip()
        content: List[str] = [title] if title else []
        i += 1
        while i < len(lines) and lines[i].startswith(">"):
            content.append(lines[i][1:].lstrip())
            i += 1
        while content and not content[-1]:
            content.pop()
        out.append(f":::{kind}")
        out.append("")
        out.extend(content)
        out.append("")
        out.append(":::")
    return "\n".join(out)


def load_unit(doc_type: str, name: str, index_path: str) -> Optional[UnitDoc]:
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    frontmatter, body = parse_frontmatter(text)
    return UnitDoc(
        doc_type=doc_type,
        name=name,
        source_path=os.path.abspath(index_path),
        frontmatter=frontmatter,
        body=body,
    )


def collect_units(docs_repo_path: str) -> List[UnitDoc]:
    root = os.path.abspath(docs_repo_path)
    units: List[UnitDoc] = []
    if not os.path.isdir(root):
        return units
    for entry in sorted(os.listdir(root)):
        if entry.startswith(".") or entry in _SKIP_ENTRIES:
            continue
        type_dir = os.path.join(root, entry)
        if not os.path.isdir(type_dir):
            continue
        if entry in SPLIT_DOC_TYPES:
            for child in sorted(os.listdir(type_dir)):
                child_dir = os.path.join(type_dir, child)
                if child.startswith(".") or not os.path.isdir(child_dir):
                    continue
                unit = load_unit(entry, child, os.path.join(child_dir, "index.md"))
                if unit:
                    units.append(unit)
            continue
        unit = load_unit(entry, entry, os.path.join(type_dir, "index.md"))
        if unit:
            units.append(unit)
    return units


def _docusaurus_frontmatter(unit: UnitDoc, sidebar_position: int) -> Dict:
    mapped: Dict = {}
    for key, value in unit.frontmatter.items():
        if key == "tags":
            continue
        if value is None:
            continue
        mapped[key] = value
    mapped["title"] = str(unit.frontmatter.get("title") or unit.name.replace("-", " ").title())
    mapped["sidebar_position"] = sidebar_position
    tags = unit.frontmatter.get("tags")
    if isinstance(tags, list) and tags:
        mapped["keywords"] = tags
    return mapped


def _dump_frontmatter(frontmatter: Dict) -> str:
    lines = ["---"]
    for key, value in frontmatter.items():
        dumped = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False).strip()
        lines.append(dumped)
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def write_docusaurus(units: List[UnitDoc], out_dir: str) -> SiteExportResult:
    docs_dir = os.path.join(out_dir, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    written: List[str] = []

    overview_types = sorted({u.doc_type for u in units if u.doc_type not in SPLIT_DOC_TYPES})
    split_types = sorted({u.doc_type for u in units if u.doc_type in SPLIT_DOC_TYPES})
    ordered_types = overview_types + split_types
    type_position = {name: index + 1 for index, name in enumerate(ordered_types)}

    for unit in units:
        if unit.doc_type in SPLIT_DOC_TYPES:
            rel_path = os.path.join(unit.doc_type, f"{unit.name}.md")
            position = (
                sorted(u.name for u in units if u.doc_type == unit.doc_type).index(unit.name) + 1
            )
        else:
            rel_path = os.path.join(f"{unit.doc_type}.md")
            position = 0
        target = os.path.join(docs_dir, rel_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        frontmatter = _docusaurus_frontmatter(unit, position)
        content = _dump_frontmatter(frontmatter) + convert_github_callouts(unit.body.rstrip()) + "\n"
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel_path)

    for doc_type in split_types:
        category = {
            "label": doc_type.replace("-", " ").title(),
            "position": type_position[doc_type],
        }
        cat_path = os.path.join(docs_dir, doc_type, "_category_.json")
        os.makedirs(os.path.dirname(cat_path), exist_ok=True)
        with open(cat_path, "w", encoding="utf-8") as fh:
            fh.write(_dump_category(category))
        written.append(os.path.join(doc_type, "_category_.json"))

    return SiteExportResult(
        out_dir=os.path.abspath(out_dir),
        fmt="docusaurus",
        pages=len(units),
        files=sorted(written),
    )


def _dump_category(data: Dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def export_site(
    docs_repo_path: str,
    out_dir: str,
    fmt: str = "docusaurus",
    on_progress=None,
) -> SiteExportResult:
    fmt_clean = (fmt or "").strip().lower()
    if fmt_clean not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported export format: {fmt!r}. Supported: {', '.join(SUPPORTED_FORMATS)}"
        )
    validate_out_path(docs_repo_path, out_dir)
    units = collect_units(docs_repo_path)
    if not units:
        raise ValueError(
            f"No documentation pages found in {docs_repo_path}. "
            "Generate docs first (each section needs an index.md)."
        )
    result = write_docusaurus(units, out_dir)
    if on_progress:
        on_progress(f"Exported {result.pages} page(s) → {result.out_dir}")
    return result
