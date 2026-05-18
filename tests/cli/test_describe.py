"""CLI smoke: ``wlb describe`` produces structured output."""

from __future__ import annotations

from typer.testing import CliRunner

from wlb.cli.main import app

runner = CliRunner()


def test_describe_runs() -> None:
    result = runner.invoke(app, ["describe"])
    assert result.exit_code == 0, result.output
    # Rich tables print plain text containing the transport names
    assert "ssh" in result.output
    assert "local" in result.output


def test_version_runs() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "windows-llm-bridge" in result.output
