"""Individual application units — not folders or developer tooling."""

from docflow.core.git_analyzer import DEFAULT_IGNORE
from docflow.core.inventory import (
    inventory_app_items,
    is_tooling_item,
    stack_items_from_payload,
)
from docflow.core.operations import DEFAULT_DOC_TYPES, discover_init_sections
from docflow.core.git_analyzer import GitAnalyzer


def test_is_tooling_item_drops_github_cli():
    assert is_tooling_item("GitHub CLI", "cli") is True
    assert is_tooling_item("github", path=".github/workflows/ci.yml") is True
    assert is_tooling_item("User", "model", "app/Models/User.php") is False


def test_inventory_lists_laravel_files_not_folders(tmp_path):
    (tmp_path / "app" / "Models").mkdir(parents=True)
    (tmp_path / "app" / "Filament" / "Resources").mkdir(parents=True)
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "app" / "Models" / "User.php").write_text("<?php class User {}\n")
    (tmp_path / "app" / "Filament" / "Resources" / "InvoiceResource.php").write_text(
        "<?php class InvoiceResource {}\n"
    )
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")

    items = inventory_app_items(str(tmp_path), DEFAULT_IGNORE)
    paths = {row["path"] for row in items}
    titles = {row["title"] for row in items}
    ids = {row["id"] for row in items}

    assert "app/Models/User.php" in paths
    assert "app/Filament/Resources/InvoiceResource.php" in paths
    assert "User" in titles
    assert "InvoiceResource" in titles
    assert "github" not in ids
    assert "models" not in ids
    assert not any(row["path"].endswith("/") for row in items)


def test_inventory_skips_cli_main_and_menu(tmp_path):
    src = tmp_path / "src" / "cli"
    src.mkdir(parents=True)
    (src / "main.py").write_text("def main():\n    pass\n")
    (src / "menu.py").write_text("def menu():\n    pass\n")
    (tmp_path / "src" / "auth").mkdir()
    (tmp_path / "src" / "auth" / "login.py").write_text("def login():\n    return True\n")

    items = inventory_app_items(str(tmp_path), DEFAULT_IGNORE)
    ids = {row["id"] for row in items}
    assert "main" not in ids
    assert "menu" not in ids
    assert "login" in ids
    assert all(row["kind"] != "module" for row in items)

    from_tree = inventory_app_items(
        str(tmp_path),
        DEFAULT_IGNORE,
        paths=["src/cli/main.py", "src/auth/login.py", "src/cli/menu.py"],
    )
    ids_tree = {row["id"] for row in from_tree}
    assert "login" in ids_tree
    assert "main" not in ids_tree


def test_stack_payload_drops_folders_and_tooling(tmp_path):
    models = tmp_path / "app" / "Models"
    models.mkdir(parents=True)
    (models / "User.php").write_text("<?php class User {}\n")

    items = stack_items_from_payload(
        {
            "items": [
                {
                    "id": "user",
                    "kind": "model",
                    "title": "User",
                    "path": "app/Models/User.php",
                    "include": True,
                },
                {
                    "id": "github-cli",
                    "kind": "cli",
                    "title": "GitHub CLI",
                    "path": ".github",
                    "include": True,
                },
                {
                    "id": "models",
                    "kind": "module",
                    "title": "Models",
                    "path": "app/Models",
                    "include": True,
                },
            ]
        },
        str(tmp_path),
    )
    ids = {row["id"] for row in items}
    assert ids == {"user"}


def test_stack_payload_keeps_other_items_and_section(tmp_path):
    billing = tmp_path / "app" / "Billing"
    billing.mkdir(parents=True)
    (billing / "AcmeGateway.php").write_text("<?php class AcmeGateway {}\n")
    items = stack_items_from_payload(
        {
            "items": [
                {
                    "id": "user",
                    "kind": "model",
                    "title": "User",
                    "path": "app/Models/User.php",
                    "section": "features",
                    "include": True,
                }
            ],
            "other_items": [
                {
                    "id": "acme-gateway",
                    "kind": "other",
                    "title": "Acme payment gateway",
                    "path": "app/Billing/AcmeGateway.php",
                    "section": "features",
                    "include": True,
                },
                {
                    "id": "github-cli",
                    "kind": "cli",
                    "title": "GitHub CLI",
                    "path": ".github",
                    "include": True,
                },
            ],
        },
        str(tmp_path),
    )
    by_id = {row["id"]: row for row in items}
    assert "github-cli" not in by_id
    assert by_id["user"]["section"] == "features"
    assert by_id["acme-gateway"]["kind"] == "other"


def test_discover_assigns_agent_section_and_other_items(tmp_path):
    from git import Repo

    app = tmp_path / "app"
    app.mkdir()
    Repo.init(app)
    models = app / "app" / "Models"
    models.mkdir(parents=True)
    (models / "User.php").write_text("<?php class User {}\n")
    billing = app / "app" / "Billing"
    billing.mkdir(parents=True)
    (billing / "AcmeGateway.php").write_text("<?php class AcmeGateway {}\n")
    (app / "composer.json").write_text("{}\n")

    analyzer = GitAnalyzer(str(app))
    candidates = discover_init_sections(
        analyzer,
        DEFAULT_DOC_TYPES,
        ignore_patterns=DEFAULT_IGNORE,
        skip_dirs=set(),
        stack_payload={
            "items": [
                {
                    "id": "architecture",
                    "kind": "overview",
                    "title": "Architecture",
                    "path": "composer.json",
                    "section": "architecture",
                    "include": True,
                },
                {
                    "id": "user",
                    "kind": "model",
                    "title": "User",
                    "path": "app/Models/User.php",
                    "section": "features",
                    "include": True,
                },
            ],
            "other_items": [
                {
                    "id": "acme-gateway",
                    "kind": "other",
                    "title": "Acme payment gateway",
                    "path": "app/Billing/AcmeGateway.php",
                    "section": "features",
                    "include": True,
                }
            ],
        },
    )
    by_name = {item.name: item for item in candidates}
    assert by_name["architecture"].doc_type == "architecture"
    assert by_name["user"].doc_type == "models"
    assert by_name["acme-gateway"].kind == "other"
    assert by_name["acme-gateway"].doc_type == "functions"
    assert by_name["acme-gateway"].included is True
    assert sum(1 for item in candidates if item.doc_type == "architecture") == 1


def test_discover_uses_agent_items_not_folders(tmp_path):
    from git import Repo

    app = tmp_path / "app"
    app.mkdir()
    Repo.init(app)
    models = app / "app" / "Models"
    models.mkdir(parents=True)
    (models / "User.php").write_text("<?php class User {}\n")

    analyzer = GitAnalyzer(str(app))
    candidates = discover_init_sections(
        analyzer,
        DEFAULT_DOC_TYPES,
        ignore_patterns=DEFAULT_IGNORE,
        skip_dirs=set(),
        stack_payload={
            "items": [
                {
                    "id": "user",
                    "kind": "model",
                    "title": "User",
                    "path": "app/Models/User.php",
                    "include": True,
                }
            ]
        },
    )
    models = [item for item in candidates if item.doc_type == "models"]
    assert [item.label for item in models] == ["User  (Eloquent model)"]
    assert models[0].file_paths == ["app/Models/User.php"]
