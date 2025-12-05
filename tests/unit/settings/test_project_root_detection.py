"""Tests for project root detection overrides."""

from __future__ import annotations

from pathlib import Path

from i4g.settings import config as settings_config


def test_detect_project_root_prefers_primary_env(monkeypatch, tmp_path):
    candidate = (tmp_path / "primary-root").resolve()
    candidate.mkdir()
    monkeypatch.setenv("I4G_PROJECT_ROOT", str(candidate))
    monkeypatch.delenv("I4G_RUNTIME__PROJECT_ROOT", raising=False)

    resolved = settings_config._detect_project_root()

    assert resolved == candidate


def test_detect_project_root_falls_back_to_runtime_env(monkeypatch, tmp_path):
    candidate = (tmp_path / "runtime-root").resolve()
    candidate.mkdir()
    monkeypatch.delenv("I4G_PROJECT_ROOT", raising=False)
    monkeypatch.setenv("I4G_RUNTIME__PROJECT_ROOT", str(candidate))

    resolved = settings_config._detect_project_root()

    assert resolved == candidate
