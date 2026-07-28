"""Every public skill tool must be callable and raise only CRMError on failure.
Would have caught delete_lead's NameError (dead since birth)."""
import importlib.util
import inspect
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SKILLS / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


crm = _load("crm_tools", "crm-db-operations/tools.py")

PUBLIC = [f for n, f in inspect.getmembers(crm, inspect.isfunction) if not n.startswith("_")]
# name -> (positional args, keyword args); extend as the catalog grows.
# update_lead is **fields-only, so its "sample field to patch" must go in kwargs,
# not as a second positional arg (that raises TypeError, not CRMError).
SAMPLE_ARGS = {
    "create_lead": (("note text", "note"), {}),
    "update_lead": ((1,), {"status": "contacted"}),
    "find_duplicate_leads": ((1,), {}), "get_lead_context": ((1,), {}), "list_leads": ((), {}),
    "score_lead": ((1,), {}), "draft_followup": ((1,), {}), "check_availability": (("2026-08-03",), {}),
    "book_appointment": ((1, "2026-08-03T18:00:00", "2026-08-03T18:45:00", "loc"), {}),
    "schedule_followup": ((1, "2026-08-04T09:00:00", "note"), {}), "find_neglected_leads": ((), {}),
    "generate_dashboard_insights": ((), {}), "merge_leads": ((1, 2), {}), "delete_lead": ((1,), {}),
}


@pytest.mark.parametrize("fn", PUBLIC, ids=lambda f: f.__name__)
def test_every_tool_raises_only_crmerror_when_backend_down(fn):
    assert fn.__name__ in SAMPLE_ARGS, f"add sample args for new tool {fn.__name__}"
    args, kwargs = SAMPLE_ARGS[fn.__name__]
    with patch.object(crm, "BASE_URL", "http://127.0.0.1:9"):  # nothing listens
        with pytest.raises(crm.CRMError):
            fn(*args, **kwargs)


def test_read_timeout_is_crmerror():
    """urlopen read-timeouts escape as TimeoutError unless _request catches them."""
    import urllib.request
    with patch.object(urllib.request, "urlopen", side_effect=TimeoutError("read timed out")):
        with pytest.raises(crm.CRMError):
            crm.list_leads()
