"""Dependency ratchet: migration exceptions may be removed, never expanded."""

import ast
import json
import re
from pathlib import Path

APP = Path(__file__).parents[2] / "app"
BASELINE = Path(__file__).with_name("legacy_dependencies.json")


def imports():
    result = {}
    for path in APP.rglob("*.py"):
        module = "app." + ".".join(path.relative_to(APP).with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        result[module] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                result[module].add(node.module)
                result[module].update(f"{node.module}.{name.name}" for name in node.names)
            elif isinstance(node, ast.Import):
                result[module].update(alias.name for alias in node.names)
    return result


def violations():
    found = set()
    for source, targets in imports().items():
        name = source.rsplit(".", 1)[-1]
        for target in targets:
            if target == "app.postgres.models" and source not in {"app.postgres.models"}:
                found.add(f"{source} -> {target}")
            if source.startswith("app.chat.") and target in {
                "app.clickhouse",
                "app.clickhouse.client",
                "app.clickhouse.queries",
            }:
                found.add(f"{source} -> {target}")
            if name == "router" and target in {
                "app.clickhouse",
                "app.clickhouse.client",
                "app.clickhouse.queries",
                "sqlmodel.select",
                "sqlalchemy.select",
                "stripe",
            }:
                found.add(f"{source} -> {target}")
            if source == "app.feasibility.workbook" and target == "app.feasibility.queries":
                found.add(f"{source} -> {target}")
            if target in {
                "app.core.normalization",
                "app.core.market_constants",
                "app.core.reelly_scope",
                "app.clickhouse.queries",
            }:
                found.add(f"{source} -> {target}")
            if (
                source.startswith("app.")
                and not source.startswith(("app.chat.", "app.main", "app.postgres.models"))
                and target.startswith("app.chat.")
                and target != "app.chat.models"
            ):
                found.add(f"{source} -> {target}")
    return sorted(found)


def test_no_new_boundary_violations():
    allowed = set(json.loads(BASELINE.read_text()))
    current = set(violations())
    assert not current - allowed, "New architectural violations: " + repr(sorted(current - allowed))
    assert not allowed - current, "Remove resolved exceptions: " + repr(sorted(allowed - current))


def test_application_imports_are_acyclic():
    graph = imports()
    graph = {name: targets & graph.keys() for name, targets in graph.items()}
    complete = set()

    def visit(name, path):
        assert name not in path, "Import cycle: " + " -> ".join([*path, name])
        if name in complete:
            return
        for target in graph[name]:
            if target != name:
                visit(target, [*path, name])
        complete.add(name)

    for module in graph:
        visit(module, [])


def test_chat_orchestration_cannot_own_warehouse_queries():
    graph = imports()
    for path in (APP / "chat").glob("*.py"):
        tree = ast.parse(path.read_text())
        module = "app.chat." + path.stem
        assert not any(target.startswith("app.clickhouse") for target in graph[module])
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value.upper()
                assert not value.lstrip().startswith(("SELECT ", "SELECT\n", "WITH\n")), path
        if path.stem != "repository":
            assert "sqlmodel.select" not in graph[module]
            assert "sqlalchemy.delete" not in graph[module]


def test_migrated_account_operations_do_not_own_sql_or_sdks():
    graph = imports()
    modules = {
        "app.auth.router",
        "app.auth.service",
        "app.auth.profile",
        "app.organization.router",
        "app.organization.service",
        "app.organization.invitations",
        "app.billing.router",
        "app.billing.webhooks",
        "app.billing.service",
        "app.admin.router",
        "app.admin.service",
        "app.token_usage.service",
    }
    forbidden = {
        "sqlmodel.select",
        "sqlalchemy.select",
        "sqlalchemy.delete",
        "stripe",
        "supabase_auth.AsyncGoTrueClient",
    }
    for module in modules:
        assert not (graph[module] & forbidden), (module, graph[module] & forbidden)


def test_crm_capabilities_keep_database_and_inventory_boundaries():
    graph = imports()
    operations = {
        "imports",
        "ownership",
        "owners",
        "inbound_imports",
        "inbound",
        "matching",
        "opportunities",
        "workspaces",
        "reports",
        "router",
    }
    for name in operations:
        module = "app.crm." + name
        assert not graph[module] & {
            "app.clickhouse.client",
            "sqlmodel.select",
            "sqlalchemy.delete",
            "sqlalchemy.text",
            "sqlalchemy.func",
        }, module
        tree = ast.parse((APP / "crm" / (name + ".py")).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(
                    r"^\s*(?:SELECT|WITH)\b[\s\S]*\bFROM\b", node.value, re.IGNORECASE
                ), module
    for source, targets in graph.items():
        if source.startswith(("app.listings.", "app.supply.")):
            assert not any(target.startswith("app.crm.") for target in targets), source
    for name in ("identity", "inbound_policy", "matching_policy", "import_parsing"):
        assert not graph["app.crm." + name] & {
            "httpx",
            "app.clickhouse.client",
            "sqlmodel.select",
            "app.postgres.client",
        }
    for name in ("service", "import_service", "inbound_service"):
        assert not (APP / "crm" / (name + ".py")).exists()


def test_project_plot_and_area_operations_do_not_own_warehouse_sql():
    graph = imports()
    for module in (
        "project.service",
        "project.radar",
        "plots.service",
        "plots.evidence",
        "plots.valuation",
        "area.service",
    ):
        assert "app.clickhouse.client" not in graph["app." + module]
        tree = ast.parse((APP / (module.replace(".", "/") + ".py")).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(
                    r"^\s*(?:SELECT|WITH)\b[\s\S]*\bFROM\b", node.value, re.IGNORECASE
                ), module
    for source, targets in graph.items():
        if source.startswith("app.plots."):
            assert not any(
                target.startswith(("app.project.", "app.feasibility.")) for target in targets
            ), source


def test_feasibility_and_inventory_capabilities_keep_io_in_their_adapters():
    graph = imports()
    operations = [
        "feasibility.service",
        "feasibility.studies",
        "feasibility.generation",
        "feasibility.research",
        "feasibility.chat",
        "feasibility.chat_agent",
        "feasibility.hbu_agent",
        "transaction.service",
        "transaction.exports",
        "transaction.facets",
        "listings.service",
        "supply.service",
        "supply.rankings",
    ]
    pure = [
        "feasibility.assumptions",
        "feasibility.normalization",
        "listings.mapping",
        "listings.policy",
        "supply.mapping",
        "supply.scoring",
    ]
    for module in operations + pure:
        assert not graph["app." + module] & {
            "app.clickhouse.client",
            "sqlmodel.select",
            "sqlalchemy.select",
            "app.postgres.client",
        }, module
        tree = ast.parse((APP / (module.replace(".", "/") + ".py")).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(
                    r"^\s*(?:SELECT|WITH)\b[\s\S]*\bFROM\b", node.value, re.IGNORECASE
                ), module
    for module in pure:
        assert not graph["app." + module] & {"httpx", "pydantic_ai.Agent", "app.core.openrouter"}
    assert "app.feasibility.generation" not in graph["app.feasibility.hbu_agent"]
    assert "app.feasibility.hbu_agent" in graph["app.feasibility.generation"]
    assert not (APP / "filters/service.py").exists()


def test_final_news_infrastructure_and_shim_boundaries():
    graph = imports()
    for module in [
        "app.news.service",
        "app.news.ingestion",
        "app.news.agent",
        "app.upload.service",
    ]:
        assert not graph[module] & {
            "sqlmodel.select",
            "sqlalchemy.select",
            "app.clickhouse.client",
        }, module
    for module in ["app.news.service", "app.news.ingestion"]:
        assert not graph[module] & {"aioboto3", "dotenv.dotenv_values"}, module
    assert not any(target.startswith("app.upload") for target in graph["app.news.ingestion"])
    for old in [
        "clickhouse/queries.py",
        "clickhouse/types.py",
        "core/normalization.py",
        "core/market_constants.py",
        "core/reelly_scope.py",
        "filters/service.py",
        "filters/models.py",
    ]:
        assert not (APP / old).exists(), old
    for source, targets in graph.items():
        if source.startswith("app.core.") and source != "app.core.dependencies":
            assert not any(
                target.startswith(
                    ("app.news.", "app.chat.", "app.crm.", "app.organization.", "app.billing.")
                )
                for target in targets
            ), source


def test_cross_domain_calls_use_public_operations_instead_of_internal_data_access():
    for path in APP.rglob("*.py"):
        source_domain = path.relative_to(APP).parts[0]
        if source_domain in {"core", "postgres"}:
            continue  # Entry composition and explicit model registration have separate guards.
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                not isinstance(node, ast.ImportFrom)
                or not node.module
                or not node.module.startswith("app.")
            ):
                continue
            target_parts = node.module.split(".")
            if target_parts[1] == source_domain:
                continue
            assert not target_parts[-1].endswith(("repository", "queries")), (path, node.module)
            assert not any(alias.name.startswith("_") for alias in node.names), (path, node.module)
