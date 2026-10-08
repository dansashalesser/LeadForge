"""Apollo's supported-technologies list: the committed excerpt and the dev script.

The full list is Apollo's data and is not vendored (user decision 2026-10-06): the
repo keeps only the rows for configured terms, and ``scripts/check_apollo_
technologies.py`` checks the configuration against the live list. Nothing here
touches the network.
"""

import importlib.util
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from leadforge.lead_ingestion.adapters import apollo
from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.errors import NormalizationError

REPO = Path(__file__).parents[5]
EXCERPT = Path(__file__).parents[2] / "fixtures" / "apollo" / apollo.TECHNOLOGY_EXCERPT
KEY = "test-key-not-real"
# The live list's shape (Category,Technology), a quoted name and a configured term.
SAMPLE = (
    "Category,Technology\n"
    "Analytics and Tracking,Google Analytics\n"
    'Database,"Couchbase"\n'
    "Database,MongoDB Atlas\n"
    "CMS,WordPress.org\n"
)


def _script() -> ModuleType:
    path = REPO / "scripts" / "check_apollo_technologies.py"
    spec = importlib.util.spec_from_file_location("check_apollo_technologies", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a technology-list test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# Verifies: specs/lead-source-adapters/requirements.md#12.12
def test_rows_are_keyed_by_the_uid_apollo_documents() -> None:
    rows = apollo.technology_rows(SAMPLE)
    assert rows["google_analytics"] == ("Analytics and Tracking", "Google Analytics")
    assert rows["wordpress_org"] == ("CMS", "WordPress.org")
    assert rows["couchbase"] == ("Database", "Couchbase")


# Verifies: specs/lead-source-adapters/requirements.md#12.12
@pytest.mark.parametrize(
    "text",
    [
        "uid,name\ndatastax,DataStax\n",
        "Category,Technology\n",
        "Category,Technology\nx,\n",
    ],
    ids=["no-technology-column", "no-rows", "blank-name"],
)
def test_an_unreadable_list_is_refused(text: str) -> None:
    with pytest.raises(NormalizationError) as caught:
        apollo.technology_rows(text)
    assert caught.value.raw_field_path == "Technology"


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_every_configured_apollo_uid_is_in_the_excerpt_and_nothing_else() -> None:
    listed = apollo.technology_rows(EXCERPT.read_text(encoding="utf-8"))
    assert set(listed) == _script().configured_uids()


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_mongodb_maps_to_both_of_apollos_mongodb_technologies() -> None:
    profile = load_catalog().to_profile(
        "mongodb", uid_source="apollo", alias_source="google_search"
    )
    vocabulary = profile.vocabulary_for("apollo")
    assert apollo.uids_of({"mongodb": vocabulary["mongodb"]}) == (
        "mongodb_atlas",
        "mongodb_realm",
    )


# Verifies: specs/lead-source-adapters/requirements.md#12.13 (property)
def test_the_script_reproduces_the_committed_excerpt_byte_for_byte() -> None:
    script = _script()
    text = EXCERPT.read_text(encoding="utf-8")
    listed = apollo.technology_rows(text)
    assert script.excerpt(listed, script.configured_uids()) == text


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_the_script_reports_configured_uids_missing_from_the_list() -> None:
    script = _script()
    listed = apollo.technology_rows(SAMPLE)
    configured = frozenset({"couchbase", "mongodb_atlas", "datastaxx"})
    assert script.missing(listed, configured) == ["datastaxx"]
    assert script.excerpt(listed, configured) == (
        "Category,Technology\nDatabase,Couchbase\nDatabase,MongoDB Atlas\n"
    )


def _run(
    argv: list[str], environ: Mapping[str, str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    script = _script()
    asked: list[str] = []

    def fetch(key: str) -> str:
        asked.append(key)
        return SAMPLE

    code = script.main(argv, fetch=fetch, environ=environ)
    out = capsys.readouterr()
    assert asked in ([], [environ.get("APOLLO_API_KEY")])
    return code, out.out + out.err


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_the_script_needs_a_key_and_never_prints_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, output = _run([], {}, capsys)
    assert code == 2
    assert "APOLLO_API_KEY" in output
    code, output = _run([], {"APOLLO_API_KEY": KEY}, capsys)
    assert KEY not in output
    # The shipped configuration names cassandra, datastax and mongodb_realm, which
    # the inline sample lacks.
    assert code == 1
    assert "datastax" in output


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_the_script_prints_excerpt_rows_and_writes_no_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _, output = _run(["--rows"], {"APOLLO_API_KEY": KEY}, capsys)
    assert "Category,Technology\nDatabase,Couchbase\nDatabase,MongoDB Atlas\n" in output
    assert list(tmp_path.iterdir()) == []


def _served(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[str], str]:
    script = _script()
    transport = httpx.MockTransport(handler)
    return lambda key: script.fetch(key, transport=transport)


# Verifies: specs/lead-source-adapters/requirements.md#12.13
def test_the_key_goes_to_apollo_only_not_to_a_redirect_target() -> None:
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("x-api-key")))
        if request.url.host == "api.apollo.io":
            return httpx.Response(302, headers={"location": "https://cdn.test/l.csv"})
        return httpx.Response(200, text=SAMPLE)

    assert _served(handler)(KEY) == SAMPLE
    assert seen == [("api.apollo.io", KEY), ("cdn.test", None)]


# Verifies: specs/lead-source-adapters/requirements.md#12.13
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (401, f"bad key {KEY} for ann@corp.test"),
        (None, f"refused {KEY} ann@corp.test"),
        (200, "<html>ann@corp.test</html>"),
    ],
    ids=["http-error", "transport-error", "not-the-list"],
)
def test_a_failed_download_exits_2_echoing_neither_key_nor_body(
    status: int | None, body: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if status is None:
            raise httpx.ConnectError(body)
        return httpx.Response(status, text=body)

    fetch = _served(handler)
    code = _script().main([], fetch=fetch, environ={"APOLLO_API_KEY": KEY})
    out = capsys.readouterr()
    output = out.out + out.err
    assert code == 2
    assert KEY not in output
    assert "corp.test" not in output


def test_the_script_has_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        _script().main(["--help"], environ={})
    assert exited.value.code == 0
    assert "--rows" in capsys.readouterr().out


# Verifies: specs/user-recognition/requirements.md#2.7
def test_the_script_reads_its_uids_from_the_given_catalog_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / "acme.yaml").write_text(
        "vendor: {key: acme, name: Acme, domains: [], partner_domains: []}\n"
        "products:\n  - key: widget\n    aliases: [{text: Widget}]\n"
        "    technology_uids: [acme_widget]\n",
        encoding="utf-8",
    )

    assert _script().configured_uids(tmp_path) == frozenset({"acme_widget"})
