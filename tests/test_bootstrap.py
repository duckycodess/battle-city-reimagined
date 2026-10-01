"""Bootstrap contract: workspace packages import, and the task form states its policy."""

import importlib
import re
from pathlib import Path

TASK_FORM = Path(__file__).resolve().parents[1] / ".github/ISSUE_TEMPLATE/implementation-task.yml"


def test_workspace_packages_import() -> None:
    for module in (
        "battle_city_sim",
        "battle_city_content",
        "battle_city_protocol",
        "battle_city_client",
        "battle_city_server",
        "battle_city_ai",
        "battle_city_tools",
    ):
        importlib.import_module(module)


def test_task_form_states_delegated_decision_authority() -> None:
    """The form must delegate ordinary decisions and must never apply a label itself."""
    form = TASK_FORM.read_text(encoding="utf-8")
    prose = " ".join(form.split())

    for phrase in (
        "Issue creation does not authorize automation.",
        "The owner applies exact agent:ready",
        "Once an issue carries exact agent:ready",
        "conservative, reversible option",
        "records the choice and its rationale",
        "escalates to agent:needs-human only when",
    ):
        assert phrase in prose, f"task form is missing: {phrase}"

    label_keys = re.findall(r"(?m)^\s*labels:", form)
    assert not label_keys, "task form must not auto-apply labels, including agent:ready"
