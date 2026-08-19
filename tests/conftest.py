import copy
import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def load_fixture():
    def load(relative: str) -> dict[str, Any]:
        return json.loads((FIXTURES / relative).read_text())

    return load


def mutate(base: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    result = copy.deepcopy(base)
    parts = path.split(".")
    target: Any = result
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    final = parts[-1]
    if isinstance(target, list):
        target[int(final)] = value
    else:
        target[final] = value
    return result
