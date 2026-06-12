import ast
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_application_and_domain_do_not_import_source_adapters() -> None:
    source_specific_fragments = [
        "infrastructure.sources.kufar",
        "infrastructure.sources.realt",
        "KufarSource",
        "RealtSource",
    ]

    for path in [
        *PROJECT_ROOT.joinpath("src/apartmentfinder/domain").rglob("*.py"),
        *PROJECT_ROOT.joinpath("src/apartmentfinder/application").rglob("*.py"),
    ]:
        text = path.read_text(encoding="utf-8")
        assert not any(fragment in text for fragment in source_specific_fragments), path


def test_pyproject_exposes_application_entrypoints() -> None:
    data = tomllib.loads(PROJECT_ROOT.joinpath("pyproject.toml").read_text())
    scripts = data["project"]["scripts"]

    assert scripts["apartmentfinder-bot"] == (
        "apartmentfinder.interfaces.telegram.bot:main"
    )
    assert scripts["apartmentfinder-worker"] == (
        "apartmentfinder.interfaces.worker.main:main"
    )


def test_telegram_bot_entrypoint_does_not_start_background_polling() -> None:
    bot_tree = ast.parse(
        PROJECT_ROOT.joinpath(
            "src/apartmentfinder/interfaces/telegram/bot.py"
        ).read_text(encoding="utf-8")
    )
    worker_tree = ast.parse(
        PROJECT_ROOT.joinpath(
            "src/apartmentfinder/interfaces/worker/main.py"
        ).read_text(encoding="utf-8")
    )

    assert "notifier_loop" not in _called_names(_function_node(bot_tree, "run_bot"))
    assert "notifier_loop" in _called_names(_function_node(worker_tree, "run_worker"))


def _function_node(
    tree: ast.Module,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ):
            return node
    raise AssertionError(f"Function {name} not found")


def _called_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names
