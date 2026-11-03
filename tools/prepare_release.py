"""Release preparation CLI for generating GitHub release notes and Markdown changelog entries."""

import asyncio
import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass

import click
import httpx
import msgspec

_polar = "[Polar.sh](https://polar.sh/litestar-org)"
_open_collective = "[OpenCollective](https://opencollective.com/litestar)"
_github_sponsors = "[GitHub Sponsors](https://github.com/sponsors/litestar-org/)"
_repository_owner = "cofin"
_repository_name = "litestar-mcp"
_repository = f"{_repository_owner}/{_repository_name}"
_repository_url = f"https://github.com/{_repository}"
_RELEASE_HEADING_RE = re.compile(r"^##\s+\[?v?\d+\.\d+\.\d+")


class PullRequest(msgspec.Struct, frozen=True, gc=False, kw_only=True):
    """GitHub pull request payload returned by the REST API."""

    title: "str"
    """Full pull request title including conventional commit prefix."""
    number: "int"
    """Pull request number."""
    body: "str"
    """Markdown body of the pull request."""
    created_at: "str"
    """ISO-8601 creation timestamp."""
    user: "RepoUser"
    """Pull request author."""
    merge_commit_sha: "str | None" = None
    """Merge commit SHA if merged."""


class Comp(msgspec.Struct, frozen=True, gc=False):
    """Commit comparison entry returned by the GitHub compare endpoint."""

    sha: "str"
    """Commit SHA."""

    class _Commit(msgspec.Struct, frozen=True, gc=False):
        """Nested commit metadata payload."""

        message: "str"
        """Commit message."""
        url: "str"
        """Commit API URL."""

    commit: "_Commit"
    """Nested commit details."""


class RepoUser(msgspec.Struct, frozen=True, gc=False):
    """GitHub user record attached to a pull request."""

    login: "str"
    """GitHub username."""
    id: "int"
    """GitHub numeric user ID."""
    type: "str"
    """Account type such as ``User`` or ``Bot``."""


@dataclass
class PRInfo:
    """Normalized pull request metadata used for release notes and changelog generation."""

    url: "str"
    """HTML URL of the pull request."""
    title: "str"
    """Original pull request title."""
    clean_title: "str"
    """Title with the conventional commit prefix stripped."""
    cc_type: "str"
    """Conventional commit type such as ``feat`` or ``fix``."""
    number: "int"
    """Pull request number."""
    closes: "list[int]"
    """Issue numbers closed by this pull request."""
    created_at: "dt.datetime"
    """Parsed pull request creation datetime."""
    description: "str"
    """Pull request body text."""
    user: "RepoUser"
    """Pull request author."""


@dataclass
class ReleaseInfo:
    """Aggregated release information between a base tag and a target release tag."""

    base: "str"
    """Previous release tag used as the comparison base."""
    release_tag: "str"
    """New release tag name (for example ``v0.18.0``)."""
    version: "str"
    """Semantic version string without the leading ``v``."""
    pull_requests: "dict[str, list[PRInfo]]"
    """Pull requests grouped by conventional commit type."""
    first_time_prs: "list[PRInfo]"
    """First-time contributor pull requests included in this release."""

    @property
    def compare_url(self) -> "str":
        """Return the GitHub comparison URL between ``base`` and ``release_tag``."""
        return f"{_repository_url}/compare/{self.base}...{self.release_tag}"


def _pr_number_from_commit(comp: "Comp") -> "int | None":
    """Extract a pull request number from a squash-merge commit subject line."""
    message_head = comp.commit.message.split("\n\n")[0]
    match = re.search(r"\(#(\d+)\)$", message_head)
    if not match:
        click.echo(f"Could not find PR number in {message_head}")
    return int(match[1]) if match else None


class _Thing:
    """GitHub API client for collecting pull request and contributor metadata."""

    def __init__(self, *, gh_token: "str", base: "str", release_branch: "str", tag: "str", version: "str") -> "None":
        """Initialize the release client for the target tag and comparison range."""
        self._gh_token = gh_token
        self._base = base
        self._new_release_tag = tag
        self._release_branch = release_branch
        self._new_release_version = version
        self._base_client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {gh_token}",
            }
        )
        self._api_client = httpx.AsyncClient(
            headers={
                **self._base_client.headers,
                "X-GitHub-Api-Version": "2022-11-28",
                "Accept": "application/vnd.github+json",
            },
            base_url=f"https://api.github.com/repos/{_repository}/",
        )

    async def get_closing_issues_references(self, pr_number: "int") -> "list[int]":
        """Query GitHub GraphQL for issue numbers closed by ``pr_number``."""
        graphql_query = """{
        repository(owner: "%s", name: "%s") {
            pullRequest(number: %d) {
                id
                closingIssuesReferences (first: 10) {
                    edges {
                        node {
                            number
                        }
                    }
                }
            }
        }
    }"""
        query = graphql_query % (_repository_owner, _repository_name, pr_number)
        res = await self._base_client.post("https://api.github.com/graphql", json={"query": query})
        if res.is_client_error:
            return []
        data = res.json()
        return [
            edge["node"]["number"]
            for edge in data["data"]["repository"]["pullRequest"]["closingIssuesReferences"]["edges"]
        ]

    async def _get_pr_info_for_pr(self, number: "int") -> "PRInfo | None":
        """Fetch and normalize pull request details for ``number``."""
        res = await self._api_client.get(f"/pulls/{number}")
        if res.is_client_error:
            click.secho(
                f"Could not get PR info for {number}.  Fetch request returned a status of {res.status_code}",
                fg="yellow",
            )
            return None
        res.raise_for_status()
        data = res.json()
        if not data["body"]:
            data["body"] = ""
        if not data:
            return None
        pr = msgspec.convert(data, type=PullRequest)

        if ":" in pr.title:
            cc_prefix, clean_title = pr.title.split(":", maxsplit=1)
            cc_type = cc_prefix.split("(", maxsplit=1)[0].lower()
        else:
            cc_type = "misc"
            clean_title = pr.title
        closes_issues = await self.get_closing_issues_references(pr_number=pr.number)

        return PRInfo(
            number=pr.number,
            cc_type=cc_type,
            clean_title=clean_title.strip(),
            url=f"{_repository_url}/pull/{pr.number}",
            closes=closes_issues,
            title=pr.title,
            created_at=dt.datetime.strptime(pr.created_at, "%Y-%m-%dT%H:%M:%S%z"),
            description=pr.body,
            user=pr.user,
        )

    async def get_prs(self) -> "dict[str, list[PRInfo]]":
        """Return non-bot pull requests in the compare range grouped by conventional commit type."""
        res = await self._api_client.get(f"/compare/{self._base}...{self._release_branch}")
        res.raise_for_status()
        compares = msgspec.convert(res.json()["commits"], list[Comp])
        pr_numbers = list(filter(None, (_pr_number_from_commit(c) for c in compares)))
        pulls = await asyncio.gather(*map(self._get_pr_info_for_pr, pr_numbers))

        prs: dict[str, list[PRInfo]] = defaultdict(list)
        for pr in pulls:
            if not pr:
                continue
            if pr.user.type != "Bot":
                prs[pr.cc_type].append(pr)
        return prs

    async def _get_first_time_contributions(self, prs: "dict[str, list[PRInfo]]") -> "list[PRInfo]":
        """Identify authors whose earliest pull request in this release is their first commit on ``main``."""
        prs_by_user_login: dict[str, list[PRInfo]] = defaultdict(list)
        for pr in [p for type_prs in prs.values() for p in type_prs]:
            prs_by_user_login[pr.user.login].append(pr)

        first_prs: list[PRInfo] = []

        async def is_user_first_commit(user_login: "str") -> "None":
            """Check whether ``user_login`` had any commits on ``main`` prior to ``first_pr``."""
            first_pr = min(prs_by_user_login[user_login], key=lambda p: p.created_at)
            res = await self._api_client.get(
                "/commits",
                params={
                    "author": user_login,
                    "sha": "main",
                    "until": first_pr.created_at.isoformat(),
                    "per_page": 1,
                },
            )
            res.raise_for_status()

            if len(res.json()) == 0:
                first_prs.append(first_pr)

        await asyncio.gather(*map(is_user_first_commit, prs_by_user_login.keys()))

        return first_prs

    async def get_release_info(self) -> "ReleaseInfo":
        """Collect all pull requests and first-time contributors for the target release."""
        prs = await self.get_prs()
        first_time_contributors = await self._get_first_time_contributions(prs)
        return ReleaseInfo(
            pull_requests=prs,
            first_time_prs=first_time_contributors,
            base=self._base,
            release_tag=self._new_release_tag,
            version=self._new_release_version,
        )

    async def create_draft_release(self, body: "str", release_branch: "str") -> "str":
        """Create a draft GitHub release and return its HTML URL."""
        res = await self._api_client.post(
            "/releases",
            json={
                "tag_name": self._new_release_tag,
                "target_commitish": release_branch,
                "name": self._new_release_tag,
                "draft": True,
                "body": body,
            },
        )
        res.raise_for_status()
        return str(res.json()["html_url"])


class GHReleaseWriter:
    """Builder for GitHub release notes Markdown."""

    def __init__(self) -> "None":
        """Initialize an empty GitHub release notes buffer."""
        self.text = ""

    def add_line(self, line: "str") -> "None":
        """Append a single line to the release notes buffer."""
        self.text += line + "\n"

    def add_pr_descriptions(self, infos: "list[PRInfo]") -> "None":
        """Append bullet items for each pull request in ``infos``."""
        for info in infos:
            self.add_line(f"* {info.title} by @{info.user.login} in {info.url}")


class ChangelogEntryWriter:
    """Builder for Keep-a-Changelog Markdown entries in ``docs/changelog.md``."""

    def __init__(self) -> "None":
        """Initialize an empty Markdown changelog buffer."""
        self.text = ""
        self._cc_type_map = {"fix": "Fixed", "feat": "Added"}

    def add_line(self, line: "str" = "") -> "None":
        """Append a line to the Markdown changelog buffer."""
        self.text += line + "\n"

    def section_for_type(self, cc_type: "str") -> "str":
        """Map a conventional commit type to its Keep-a-Changelog section heading."""
        return self._cc_type_map.get(cc_type, "Changed")

    def add_section(self, heading: "str", prs: "list[PRInfo]") -> "None":
        """Render a Keep-a-Changelog subsection (`### Added`, `### Fixed`, `### Changed`)."""
        if not prs:
            return
        self.add_line(f"### {heading}")
        self.add_line()
        for pr in prs:
            self.add_change(pr)
        self.add_line()

    def add_change(self, pr: "PRInfo") -> "None":
        """Render a single pull request entry as a Markdown bullet item with PR and issue links."""
        refs = [f"[#{pr.number}]({pr.url})"]
        refs.extend(f"[#{issue}]({_repository_url}/issues/{issue})" for issue in pr.closes)
        ref_suffix = f" ({', '.join(refs)})"
        self.add_line(f"- **{pr.clean_title}**{ref_suffix}")
        description = pr.description.strip()
        if description:
            self.add_line()
            for line in description.splitlines():
                self.add_line(f"  {line}" if line.strip() else "")


def build_gh_release_notes(release_info: "ReleaseInfo") -> "str":
    """Build categorized GitHub release notes Markdown from ``release_info``."""
    doc = GHReleaseWriter()

    doc.add_line("## What's changed")
    if features := release_info.pull_requests.get("feat"):
        doc.add_line("\n### New features 🚀")
        doc.add_pr_descriptions(features)
    if fixes := release_info.pull_requests.get("fix"):
        doc.add_line("\n### Bugfixes 🐛")
        doc.add_pr_descriptions(fixes)
    if release_info.first_time_prs:
        doc.add_line("\n## New contributors 🎉")
        for pr in release_info.first_time_prs:
            doc.add_line(f"* @{pr.user.login} made their first contribution in {pr.url}")

    ignore_sections = {"fix", "feat", "ci", "chore"}

    if other := [pr for k, prs in release_info.pull_requests.items() if k not in ignore_sections for pr in prs]:
        doc.add_line("\n<!-- Review these: Not all of them should go into the release notes -->")
        doc.add_line("### Other changes")
        doc.add_pr_descriptions(other)

    doc.add_line("\n**Full Changelog**")
    doc.add_line(release_info.compare_url)

    return doc.text


def build_changelog_entry(release_info: "ReleaseInfo", interactive: "bool" = False) -> "str":
    """Build a Keep-a-Changelog Markdown section for ``docs/changelog.md``."""
    doc = ChangelogEntryWriter()
    release_date = dt.datetime.now(tz=dt.timezone.utc).date().isoformat()
    doc.add_line(f"## [{release_info.version}] - {release_date}")
    doc.add_line()

    grouped: dict[str, list[PRInfo]] = {"Added": [], "Fixed": [], "Changed": []}
    change_types = {"fix", "feat"}
    for prs in release_info.pull_requests.values():
        for pr in prs:
            cc_type = pr.cc_type
            if cc_type in change_types or (interactive and click.confirm(f"Include PR #{pr.number} {pr.title!r}?")):
                grouped[doc.section_for_type(cc_type)].append(pr)
            else:
                click.secho(f"Ignoring change with type {cc_type}", fg="yellow")

    for section in ("Added", "Fixed", "Changed"):
        doc.add_section(section, grouped[section])

    return doc.text.rstrip() + "\n"


def _get_gh_token() -> "str":
    """Resolve a GitHub token from ``GH_TOKEN`` or the ``gh`` CLI."""
    if gh_token := os.getenv("GH_TOKEN"):
        click.secho("Using GitHub token from env", fg="blue")
        return gh_token

    gh_executable = shutil.which("gh")
    if not gh_executable:
        click.secho("GitHub CLI not installed", fg="yellow")
    else:
        click.secho("Using GitHub CLI to obtain GitHub token", fg="blue")
        proc = subprocess.run([gh_executable, "auth", "token"], check=True, capture_output=True, text=True)
        if out := (proc.stdout or "").strip():
            return out

    click.secho("Could not find any GitHub token", fg="red")
    sys.exit(1)


def _get_latest_tag() -> "str":
    """Return the most recent git tag sorted by tagger date."""
    click.secho("Using latest tag", fg="blue")
    git_executable = shutil.which("git") or "git"
    output = subprocess.run(
        [git_executable, "tag", "--sort=taggerdate"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tags = [line.strip() for line in output.splitlines() if line.strip()]
    return tags[-1] if tags else ""


def _write_changelog_entry(changelog_entry: "str", changelog_path: "pathlib.Path | None" = None) -> "None":
    """Insert ``changelog_entry`` above the most recent release heading in ``docs/changelog.md``."""
    target_path = changelog_path or pathlib.Path("docs/changelog.md")
    changelog_lines = target_path.read_text(encoding="utf-8").splitlines()
    line_no = next(
        (i for i, line in enumerate(changelog_lines) if _RELEASE_HEADING_RE.match(line)),
        None,
    )
    if line_no is None:
        line_no = next(
            (i for i, line in enumerate(changelog_lines) if line.startswith("## ")),
            None,
        )
    if line_no is None:
        msg = "Changelog start not found in docs/changelog.md"
        raise ValueError(msg)

    entry_lines = changelog_entry.rstrip("\n").splitlines()
    changelog_lines[line_no:line_no] = [*entry_lines, ""]
    target_path.write_text("\n".join(changelog_lines) + "\n", encoding="utf-8")


@click.command()
@click.argument("version")
@click.option("--base", help="Previous release tag. Defaults to the latest tag")
@click.option("--branch", help="Release branch", default="main")
@click.option(
    "--gh-token",
    help="GitHub token. If not provided, read from the GH_TOKEN env variable. "
    "Alternatively, if the GitHub CLI is installed, it will be used to fetch a token",
)
@click.option(
    "-i",
    "--interactive",
    is_flag=True,
    help="Interactively decide which commits should be included in the release notes",
    default=False,
)
@click.option("-c", "--create-draft-release", is_flag=True, help="Create draft release on GitHub")
def cli(
    base: "str | None",
    branch: "str",
    version: "str",
    gh_token: "str | None",
    interactive: "bool",
    create_draft_release: "bool",
) -> "None":
    """Generate release notes and prepend a Markdown changelog entry in ``docs/changelog.md``."""
    if gh_token is None:
        gh_token = _get_gh_token()
    if base is None:
        base = _get_latest_tag()

    if not re.match(r"\d+\.\d+\.\d+", version):
        click.secho(f"Invalid version: {version!r}")
        sys.exit(1)

    new_tag = f"v{version}"

    click.secho(f"Creating release notes for tag {new_tag}, using {base} as a base", fg="cyan")

    thing = _Thing(gh_token=gh_token, base=base, release_branch=branch, tag=new_tag, version=version)
    loop = asyncio.new_event_loop()

    release_info = loop.run_until_complete(thing.get_release_info())
    gh_release_notes = build_gh_release_notes(release_info)
    changelog_entry = build_changelog_entry(release_info, interactive=interactive)

    click.secho("Writing changelog entry", fg="green")
    _write_changelog_entry(changelog_entry)

    if create_draft_release:
        click.secho("Creating draft release", fg="blue")
        release_url = loop.run_until_complete(thing.create_draft_release(body=gh_release_notes, release_branch=branch))
        click.echo(f"Draft release available at: {release_url}")
    else:
        click.echo(gh_release_notes)

    loop.close()


if __name__ == "__main__":
    cli()
