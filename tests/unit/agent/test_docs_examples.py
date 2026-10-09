import importlib.util
from pathlib import Path

from litestar import Litestar


def test_coffee_shop_agent_example() -> None:
    example_path = Path(__file__).parents[3] / "docs" / "examples" / "agent" / "coffee_shop_agent.py"
    assert example_path.exists()

    spec = importlib.util.spec_from_file_location("coffee_shop_agent", example_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["coffee_shop_agent"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("coffee_shop_agent", None)

    app = getattr(module, "app", None)
    assert isinstance(app, Litestar)
