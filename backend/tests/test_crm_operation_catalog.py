import importlib.util
import json
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "crm-db-operations"
CATALOG = SKILL_DIR / "operations.json"
PLUGIN_CATALOG = REPO_ROOT / "openclaw-plugins" / "openhouse-crm" / "operations.json"


def _load_cli():
    original_path = list(sys.path)
    previous_tools = sys.modules.pop("tools", None)
    try:
        sys.path.insert(0, str(SKILL_DIR))
        spec = importlib.util.spec_from_file_location("crm_operations_cli", SKILL_DIR / "cli.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = original_path
        sys.modules.pop("tools", None)
        if previous_tools is not None:
            sys.modules["tools"] = previous_tools


def test_catalog_is_valid_and_drives_cli_dispatch():
    names = json.loads(CATALOG.read_text(encoding="utf-8"))

    assert isinstance(names, list)
    assert names
    assert len(names) == len(set(names))
    assert all(isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)

    cli = _load_cli()
    assert cli._load_operation_names() == tuple(names)
    assert set(cli.OPERATIONS) == set(names)
    assert all(callable(function) for function in cli.OPERATIONS.values())


def test_plugin_operation_catalog_matches_python_catalog_exactly():
    assert json.loads(PLUGIN_CATALOG.read_text(encoding="utf-8")) == json.loads(
        CATALOG.read_text(encoding="utf-8")
    )
