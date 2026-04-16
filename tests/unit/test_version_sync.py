"""Guard that the runtime version constant matches the packaged version."""

from importlib.metadata import version as pkg_version

from litestar_mcp.__metadata__ import __version__


def test_runtime_version_matches_distribution() -> None:
    assert __version__ == pkg_version("litestar-mcp")
