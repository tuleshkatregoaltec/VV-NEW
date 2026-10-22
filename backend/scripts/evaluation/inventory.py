"""Generate a static evidence map without importing or executing application code.

Resolved Python calls are possible edges, not proof of runtime reachability.
Dynamic calls remain visible as unresolved expressions. SQL references are lexical
candidates, not a SQL parser's authoritative list (CTEs can also appear).
"""

import ast
import hashlib
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def build_inventory(app: Path = APP) -> dict:
    functions, agents, registrations, invocations, modules = {}, [], [], [], {}
    for path in sorted(app.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        module = "app." + ".".join(path.relative_to(app).with_suffix("").parts)
        module = module.removesuffix(".__init__")
        location = str(path.relative_to(app.parent))
        aliases = {}
        assignments = {}
        local_names = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for name in node.names:
                    aliases[name.asname or name.name] = f"{node.module}.{name.name}"
            elif isinstance(node, ast.Import):
                for name in node.names:
                    aliases[name.asname or name.name.split(".")[0]] = (
                        name.name if name.asname else name.name.split(".")[0]
                    )
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value

        def resolve(node):
            name = dotted(node)
            if not name:
                return None
            head, *tail = name.split(".")
            base = aliases.get(head, f"{module}.{head}" if head in local_names else None)
            return ".".join([base, *tail]) if base else None

        def strings(node, seen=frozenset()):
            if isinstance(node, ast.Name) and node.id in assignments and node.id not in seen:
                return strings(assignments[node.id], seen | {node.id})
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return " ".join(strings(child, seen) for child in ast.iter_child_nodes(node))

        modules[module] = {
            "file": location,
            "sha256": digest(source),
            "imports": sorted(set(aliases.values())),
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node in tree.body:
                calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
                sql_text = " ".join(
                    strings(call) for call in calls if dotted(call.func) in {"query", "query_df"}
                )
                functions[f"{module}.{node.name}"] = {
                    "file": location,
                    "line": node.lineno,
                    "sha256": digest(ast.dump(node, include_attributes=False)),
                    "signature": ast.unparse(node.args),
                    "description": ast.get_docstring(node),
                    "model_configuration_references": sorted(
                        {
                            ast.unparse(child)
                            for child in ast.walk(node)
                            if isinstance(child, ast.Attribute)
                            and child.attr == "DEFAULT_CHAT_MODEL"
                        }
                    ),
                    "provider_endpoints": sorted(
                        {
                            child.value
                            for child in ast.walk(node)
                            if isinstance(child, ast.Constant)
                            and isinstance(child.value, str)
                            and child.value.startswith("https://")
                            and "/chat/completions" in child.value
                        }
                    ),
                    "calls": sorted({value for call in calls if (value := resolve(call.func))}),
                    "unresolved_calls": sorted(
                        {ast.unparse(call.func) for call in calls if resolve(call.func) is None}
                    ),
                    "sql_source_candidates": sorted(
                        set(re.findall(r"\b(?:FROM|JOIN)\s+([a-zA-Z_]\w*)", sql_text, re.I))
                    ),
                }
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Attribute) and decorator.attr in {
                        "tool",
                        "tool_plain",
                    }:
                        registrations.append(
                            {
                                "agent": f"{module}.{ast.unparse(decorator.value)}",
                                "function": f"{module}.{node.name}",
                                "file": location,
                                "line": decorator.lineno,
                            }
                        )
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                if resolve(node.value.func) == "pydantic_ai.Agent":
                    for target in node.targets:
                        prompt = next(
                            (
                                keyword.value
                                for keyword in node.value.keywords
                                if keyword.arg == "system_prompt"
                            ),
                            None,
                        )
                        agents.append(
                            {
                                "id": f"{module}.{ast.unparse(target)}",
                                "file": location,
                                "line": node.lineno,
                                "prompt_sha256": digest(strings(prompt))
                                if prompt is not None
                                else None,
                                "declaration_sha256": digest(
                                    ast.dump(node.value, include_attributes=False)
                                ),
                            }
                        )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"tool", "tool_plain"} and node.args:
                    registrations.append(
                        {
                            "agent": f"{module}.{ast.unparse(node.func.value)}",
                            "function": resolve(node.args[0]) or ast.unparse(node.args[0]),
                            "file": location,
                            "line": node.lineno,
                        }
                    )
                elif node.func.attr in {"run", "iter", "run_stream", "run_sync"}:
                    invocations.append(
                        {
                            "expression": ast.unparse(node.func),
                            "file": location,
                            "line": node.lineno,
                            "resolved_callable": resolve(node.func),
                        }
                    )

    # Link source-derived tool adapters to application operations and query functions.
    for agent in agents:
        agent["tools"] = [
            registration for registration in registrations if registration["agent"] == agent["id"]
        ]
        reachable = set()
        pending = [tool["function"] for tool in agent["tools"]]
        while pending:
            name = pending.pop()
            if name in reachable or name not in functions:
                continue
            reachable.add(name)
            pending.extend(functions[name]["calls"])
        agent["possible_reachable_functions"] = sorted(reachable)
    return {
        "kind": "static_source_evidence",
        "limitations": [
            "Declarations do not establish runtime selection; inspect invocation sites and traces.",
            "Dynamic aliases, object methods and conditional calls require runtime evidence.",
            "Function edges cover module-level functions; nested/class methods are unresolved.",
            "Signatures are source schemas, not generated Pydantic JSON Schema.",
            "SQL source candidates can include CTEs; computed SQL may be incomplete.",
        ],
        "agents": agents,
        "registrations": registrations,
        "invocation_candidates": invocations,
        "modules": modules,
        "functions": functions,
    }
