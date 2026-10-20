#!/usr/bin/env bash
# Remote Control server: lets the owner start sessions on this machine from the
# Claude mobile app or claude.ai/code.
#
# Why CLAUDE_CODE_OAUTH_TOKEN is unset
# ------------------------------------
# The installer exports CLAUDE_CODE_OAUTH_TOKEN from ~/.bashrc so the fleet's
# agents can authenticate. Remote Control does NOT accept it: it needs the
# claude.ai subscription login in ~/.claude/.credentials.json, and a token in the
# environment SHADOWS that login ("You must be logged in to use Remote Control").
# Measured 2026-08-30: with the variable set the command refuses; unset, the same
# command starts. A systemd unit does not read ~/.bashrc, so this line only
# matters when someone runs the script by hand from an interactive shell.
unset CLAUDE_CODE_OAUTH_TOKEN

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR="${MARVEEN_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"

# Why the Telegram state directory is redirected to an EMPTY one
# --------------------------------------------------------------
# Remote Control is not just a server: on start it creates a session in this
# directory, and every session started from the phone lands here too. Those are
# full Claude sessions, so they read this project's .claude/settings.json, where
# `enabledPlugins.telegram@claude-plugins-official` is true -- and they start a
# SECOND Telegram poller on the SAME bot token as the main agent. Telegram allows
# exactly one getUpdates consumer per token, so the two knock each other out with
# 409 Conflict.
#
# This is not theory. On 2026-08-30 the service started, and twenty-five seconds
# later the main agent's Telegram MCP connection was gone; the incident report
# had to go out through the Bot API instead of the channel.
#
# Turning the plugin off in the project settings is NOT an option: channels.sh
# documents (line ~317) that Claude Code only starts a channel plugin marked true
# there, so the main agent's own `--channels` flag would stop working with it.
#
# The plugin resolves its state directory as
#   TELEGRAM_STATE_DIR ?? <CLAUDE_CONFIG_DIR|~/.claude>/channels/telegram
# and exits 1 straight away when it finds no TELEGRAM_BOT_TOKEN there. So
# pointing it at an empty directory makes every session spawned here start
# WITHOUT a Telegram channel -- which is correct: the owner talks to these
# sessions through the phone app, not through the bot -- and the main agent keeps
# the token to itself. Nothing shared is modified.
export TELEGRAM_STATE_DIR="${TELEGRAM_STATE_DIR:-$HOME/.marveen-remote-control/telegram-state}"

cd "$REPO_DIR"

# The spawn mode and the one-time consent are remembered in ~/.claude.json
# (remoteControlSpawnMode, remoteDialogSeen), so this does not prompt. The mode
# is passed explicitly anyway: same-dir is a deliberate choice, because the point
# of remote access here is to reach the LIVE install -- its store/, its database,
# its tokens -- which an isolated worktree would not carry.
exec "$CLAUDE_BIN" rc --spawn=same-dir
