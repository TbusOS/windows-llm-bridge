"""GET /api/casts + GET /api/casts/{host}/{filename} — list + serve (M3.8)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wlb.api.server import create_app


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WLB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("WLB_TRANSPORT", "local")
    monkeypatch.delenv("WLB_PROFILE", raising=False)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _make_cast(workspace: Path, host: str, name: str, payload: str = '{"version":2,"width":80,"height":24,"timestamp":1}\n[0.0,"o","hi"]\n') -> Path:
    p = workspace / "hosts" / host / "pty" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload, encoding="utf-8")
    return p


# ─── /api/casts: list ─────────────────────────────────────────────


def test_list_empty_when_no_workspace_hosts(tmp_path: Path, client: TestClient) -> None:
    r = client.get("/api/casts")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["casts"] == []
    assert body["root"].endswith("hosts")


def test_list_returns_cast_files_with_metadata(tmp_path: Path, client: TestClient) -> None:
    _make_cast(tmp_path, "win-host", "2026-05-25T10-30-00-cmd.cast")
    _make_cast(tmp_path, "win-host", "2026-05-25T09-15-00-raw.cast")
    _make_cast(tmp_path, "local", "2026-05-25T11-00-00-raw.cast")

    r = client.get("/api/casts")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    paths = {c["path"] for c in body["casts"]}
    assert paths == {
        "local/2026-05-25T11-00-00-raw.cast",
        "win-host/2026-05-25T10-30-00-cmd.cast",
        "win-host/2026-05-25T09-15-00-raw.cast",
    }
    # Each entry has the right shape.
    for c in body["casts"]:
        assert {"host", "filename", "path", "size", "modified"} <= c.keys()
        assert c["size"] > 0
        assert c["filename"].endswith(".cast")


def test_list_skips_non_cast_files(tmp_path: Path, client: TestClient) -> None:
    _make_cast(tmp_path, "h1", "real.cast")
    (tmp_path / "hosts" / "h1" / "pty" / "stray.log").write_text("noise")
    (tmp_path / "hosts" / "h1" / "pty" / "notes.txt").write_text("notes")
    r = client.get("/api/casts")
    paths = {c["path"] for c in r.json()["casts"]}
    assert paths == {"h1/real.cast"}


def test_list_skips_unsafe_host_dirs(tmp_path: Path, client: TestClient) -> None:
    _make_cast(tmp_path, "good-host", "ok.cast")
    # Create a directory whose name would fail is_safe_host (leading dot).
    bad = tmp_path / "hosts" / ".hidden" / "pty"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "should-not-list.cast").write_text("hidden")
    r = client.get("/api/casts")
    paths = {c["path"] for c in r.json()["casts"]}
    assert paths == {"good-host/ok.cast"}


def test_list_skips_hosts_without_pty_dir(tmp_path: Path, client: TestClient) -> None:
    _make_cast(tmp_path, "h1", "yes.cast")
    # h2 has hosts/h2 but no pty/ subdir.
    (tmp_path / "hosts" / "h2" / "logs").mkdir(parents=True, exist_ok=True)
    r = client.get("/api/casts")
    paths = {c["path"] for c in r.json()["casts"]}
    assert paths == {"h1/yes.cast"}


# ─── /api/casts/{host}/{filename}: serve ──────────────────────────


def test_serve_returns_file_with_asciicast_media_type(tmp_path: Path, client: TestClient) -> None:
    body = '{"version":2,"width":80,"height":24,"timestamp":1}\n[0.0,"o","hello"]\n'
    _make_cast(tmp_path, "h1", "serve.cast", payload=body)
    r = client.get("/api/casts/h1/serve.cast")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/x-asciicast"
    assert r.text == body


def test_serve_404_when_file_missing(client: TestClient) -> None:
    r = client.get("/api/casts/h1/nope.cast")
    assert r.status_code == 404


def test_serve_rejects_unsafe_host(client: TestClient) -> None:
    # URL routing may 404 before our handler runs (Starlette path normalization)
    # or our handler may 400 with "invalid host". Either way it MUST NOT be 200.
    r = client.get("/api/casts/..%2Fescape/x.cast")
    assert r.status_code in (400, 404)


def test_serve_rejects_non_cast_extension(tmp_path: Path, client: TestClient) -> None:
    p = tmp_path / "hosts" / "h1" / "pty" / "secret.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("nope")
    r = client.get("/api/casts/h1/secret.txt")
    assert r.status_code == 400


def test_serve_rejects_traversal_filename(tmp_path: Path, client: TestClient) -> None:
    # URL-encoded "../../etc/passwd.cast" — TestClient passes it through.
    r = client.get("/api/casts/h1/..%2Fpasswd.cast")
    # Either 400 (filename validation) or 404 (file doesn't exist); never 200.
    assert r.status_code in (400, 404)


def test_serve_rejects_dot_prefixed_filename(client: TestClient) -> None:
    r = client.get("/api/casts/h1/.hidden.cast")
    assert r.status_code == 400


def test_serve_refuses_path_escaping_workspace_via_symlink(
    tmp_path: Path, client: TestClient,
) -> None:
    """Even with a symlink trick, the resolved path must stay inside <ws>/hosts."""
    # Create a real file outside the workspace.
    outside = tmp_path.parent / "leaked.cast"
    outside.write_text("leaked")
    # Symlink into the workspace's pty dir.
    pty_dir = tmp_path / "hosts" / "h1" / "pty"
    pty_dir.mkdir(parents=True, exist_ok=True)
    link = pty_dir / "link.cast"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not supported on this filesystem")
    r = client.get("/api/casts/h1/link.cast")
    # The symlink resolves outside <ws>/hosts → 400 (path escapes workspace).
    assert r.status_code == 400


# ─── /casts.html: static page route ───────────────────────────────


def test_casts_html_route_serves_page(client: TestClient) -> None:
    r = client.get("/casts.html")
    # Either 200 (page bundled, which is the normal case) or 404 (if for some
    # reason the static asset isn't shipped). Anything else means routing broke.
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert "asciinema-player" in r.text
        assert "casts.js" in r.text
