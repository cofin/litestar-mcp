from __future__ import annotations

from typing import Any

import pytest
from tools.prepare_release import ReleaseInfo, _Thing


def test_release_info_compare_url_uses_live_repository() -> None:
    release = ReleaseInfo(
        base="v0.1.0",
        release_tag="v0.2.0",
        version="0.2.0",
        pull_requests={},
        first_time_prs=[],
    )

    assert release.compare_url == "https://github.com/cofin/litestar-mcp/compare/v0.1.0...v0.2.0"


def test_release_client_uses_live_repository_api_base() -> None:
    thing = _Thing(gh_token="token", base="v0.1.0", release_branch="main", tag="v0.2.0", version="0.2.0")

    assert str(thing._api_client.base_url) == "https://api.github.com/repos/cofin/litestar-mcp/"


@pytest.mark.asyncio
async def test_release_closing_issues_query_uses_live_repository_owner() -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        is_client_error = False

        def json(self) -> dict[str, Any]:
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "closingIssuesReferences": {"edges": []},
                        },
                    },
                },
            }

    class FakeClient:
        async def post(self, url: str, *, json: dict[str, Any]) -> FakeResponse:
            captured["url"] = url
            captured["query"] = json["query"]
            return FakeResponse()

    thing = _Thing(gh_token="token", base="v0.1.0", release_branch="main", tag="v0.2.0", version="0.2.0")
    thing._base_client = FakeClient()  # type: ignore[assignment]

    assert await thing.get_closing_issues_references(123) == []
    assert captured["url"] == "https://api.github.com/graphql"
    assert 'repository(owner: "cofin", name: "litestar-mcp")' in captured["query"]
