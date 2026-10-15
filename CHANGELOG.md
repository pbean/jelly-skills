# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and each skill is versioned independently in its `SKILL.md` metadata.

## [Unreleased]

### context-budget

- Added the `context-budget` skill (0.1.0) with `ctxcheck`, built on the `ctxlib` package.
  - It reads Claude Code and Codex CLI sessions.
  - It derives per-role handoff gates (coordinator, worker, planner) from the
    window.
  - It exits 3 when the runtime can't be measured.
  - Codex usage is `last_token_usage.total_tokens`, which matches the
    TUI.
  - The Claude window comes from env and model rules.
  - Includes a stdlib `unittest` suite under `tests/`.
