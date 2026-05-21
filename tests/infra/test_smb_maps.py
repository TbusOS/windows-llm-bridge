"""Tests for wlb.infra.smb_maps: parsing + translation."""

from __future__ import annotations

import pytest

from wlb.infra.smb_maps import (
    SmbMap,
    looks_like_linux_path,
    looks_like_windows_path,
    merge,
    parse_env_value,
    parse_toml_array,
    translate_linux_to_windows,
    translate_windows_to_linux,
)


# ─── parsing ──────────────────────────────────────────────────────


def test_parse_env_value_empty_returns_empty() -> None:
    assert parse_env_value(None) == []
    assert parse_env_value("") == []


def test_parse_env_value_single_pair() -> None:
    maps = parse_env_value("/mnt/win-share=C:\\share")
    assert len(maps) == 1
    assert maps[0].linux_mount == "/mnt/win-share"
    assert maps[0].windows_path == "C:\\share"


def test_parse_env_value_multiple_pairs() -> None:
    maps = parse_env_value("/mnt/a=C:\\a;/mnt/b=D:\\b")
    assert len(maps) == 2
    assert maps[0].windows_path == "C:\\a"
    assert maps[1].windows_path == "D:\\b"


def test_parse_env_value_strips_trailing_separators() -> None:
    maps = parse_env_value("/mnt/win-share/=C:\\share\\")
    assert maps[0].linux_mount == "/mnt/win-share"
    assert maps[0].windows_path == "C:\\share"


def test_parse_env_value_normalizes_forward_slash_in_windows() -> None:
    """Some users will write C:/share instead of C:\\share — both should work."""
    maps = parse_env_value("/mnt/win-share=C:/share")
    assert maps[0].windows_path == "C:\\share"


def test_parse_env_value_drops_malformed_entries() -> None:
    # No '=', empty sides
    maps = parse_env_value("no-equals;=/empty-linux;/empty-windows=;ok=C:\\x")
    assert len(maps) == 1
    assert maps[0].linux_mount == "ok"


def test_parse_toml_array_happy_path() -> None:
    arr = [
        {"linux": "/mnt/a", "windows": "C:\\a"},
        {"linux": "/mnt/b", "windows": "D:\\b"},
    ]
    maps = parse_toml_array(arr)
    assert len(maps) == 2
    assert maps[0].linux_mount == "/mnt/a"


def test_parse_toml_array_skips_non_dict_and_missing_keys() -> None:
    arr = [
        {"linux": "/mnt/a", "windows": "C:\\a"},
        "not a dict",
        {"linux": "/mnt/b"},                       # missing windows
        {"windows": "C:\\c"},                      # missing linux
        {"linux": 42, "windows": "C:\\d"},         # wrong type
    ]
    maps = parse_toml_array(arr)
    assert len(maps) == 1
    assert maps[0].linux_mount == "/mnt/a"


def test_merge_env_first_wins() -> None:
    env = [SmbMap("/mnt/a", "C:\\a")]
    prof = [SmbMap("/mnt/a", "Z:\\a"), SmbMap("/mnt/b", "D:\\b")]
    out = merge(env, prof)
    assert len(out) == 2
    assert out[0].windows_path == "C:\\a"   # env wins
    assert out[1].linux_mount == "/mnt/b"


def test_merge_dedups_by_windows_case_insensitive() -> None:
    env = [SmbMap("/mnt/a", "C:\\share")]
    prof = [SmbMap("/mnt/different", "c:\\SHARE")]   # same windows path, diff case
    out = merge(env, prof)
    assert len(out) == 1


# ─── translation: linux → windows ────────────────────────────────


@pytest.fixture
def maps() -> list[SmbMap]:
    return [
        SmbMap("/mnt/win-share", "C:\\share"),
        SmbMap("/mnt/factory", "D:\\factory"),
    ]


def test_linux_to_windows_exact_match(maps: list[SmbMap]) -> None:
    assert translate_linux_to_windows("/mnt/win-share", maps) == "C:\\share"


def test_linux_to_windows_under_mount(maps: list[SmbMap]) -> None:
    assert (
        translate_linux_to_windows("/mnt/win-share/build/fw.bin", maps)
        == "C:\\share\\build\\fw.bin"
    )


def test_linux_to_windows_trailing_slash_normalized(maps: list[SmbMap]) -> None:
    assert (
        translate_linux_to_windows("/mnt/win-share/sub/", maps)
        == "C:\\share\\sub"
    )


def test_linux_to_windows_no_match_returns_none(maps: list[SmbMap]) -> None:
    assert translate_linux_to_windows("/var/log/x.txt", maps) is None


def test_linux_to_windows_prefix_collision_safe(maps: list[SmbMap]) -> None:
    """/mnt/win-share-2 should NOT match /mnt/win-share even though it shares the prefix."""
    assert translate_linux_to_windows("/mnt/win-share-2/x", maps) is None


# ─── translation: windows → linux ────────────────────────────────


def test_windows_to_linux_exact_match(maps: list[SmbMap]) -> None:
    assert translate_windows_to_linux("C:\\share", maps) == "/mnt/win-share"


def test_windows_to_linux_under_path(maps: list[SmbMap]) -> None:
    assert (
        translate_windows_to_linux("C:\\share\\build\\fw.bin", maps)
        == "/mnt/win-share/build/fw.bin"
    )


def test_windows_to_linux_case_insensitive(maps: list[SmbMap]) -> None:
    assert (
        translate_windows_to_linux("c:\\SHARE\\sub", maps)
        == "/mnt/win-share/sub"
    )


def test_windows_to_linux_forward_slashes_accepted(maps: list[SmbMap]) -> None:
    assert (
        translate_windows_to_linux("C:/share/build", maps)
        == "/mnt/win-share/build"
    )


def test_windows_to_linux_no_match_returns_none(maps: list[SmbMap]) -> None:
    assert translate_windows_to_linux("C:\\Windows\\System32", maps) is None


# ─── path-shape heuristics ───────────────────────────────────────


def test_looks_like_linux_path() -> None:
    assert looks_like_linux_path("/mnt/x")
    assert looks_like_linux_path("~/notes")
    assert not looks_like_linux_path("C:\\x")
    assert not looks_like_linux_path("relative\\path")
    assert not looks_like_linux_path("")


def test_looks_like_windows_path() -> None:
    assert looks_like_windows_path("C:\\x")
    assert looks_like_windows_path("D:/y")
    assert looks_like_windows_path("\\\\server\\share")
    assert not looks_like_windows_path("/mnt/x")
    assert not looks_like_windows_path("")
