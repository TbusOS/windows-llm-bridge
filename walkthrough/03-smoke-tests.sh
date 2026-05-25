#!/usr/bin/env bash
# walkthrough/03-smoke-tests.sh
#
# Scripted smoke test for the SSH transport against the paired Windows
# host. Run after 02-linux-pair.sh is green.
#
# What gets tested (each is one "case", numbered):
#   1. wlb status                          — connection + powershell probe
#   2. wlb cmd "ver"                       — cmd.exe round trip
#   3. wlb powershell "..."                — pwsh / powershell.exe round trip
#   4. wlb fs push <tmp> + pull            — SFTP round trip with content check
#   5. wlb tool list / show / run          — declarative tool runner
#
# Each case prints "[PASS] <n>" or "[FAIL] <n>" and the test continues
# to the next regardless. Final line is "N/5 passed; exit 0/1".
#
# All output is also tee'd to walkthrough/local-smoke-<ts>.log
# (gitignored — safe to keep on disk for your audit trail).

set -uo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"
export PYTHONNOUSERSITE=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$SCRIPT_DIR/local-notes.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "FAIL: $ENV_FILE missing — run walkthrough/02-linux-pair.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

TS="$(date -u +%Y-%m-%dT%H-%M-%S)"
LOG_FILE="$SCRIPT_DIR/local-smoke-$TS.log"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0

bold() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
pass() { printf '\033[32m[PASS]\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
miss() { printf '\033[31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }

run_wlb() {
    cd "$REPO_ROOT"
    uv run wlb --profile "$WLB_PROFILE" "$@"
}

{
echo "=== walkthrough smoke run @ $TS UTC ==="
echo "  WIN_HOST=$WIN_HOST  WIN_USER=$WIN_USER  WLB_PROFILE=$WLB_PROFILE"
echo

# ── Case 1: status ──────────────────────────────────────────────────
bold "Case 1 — wlb status"
if run_wlb status; then
    pass "1 — status"
else
    miss "1 — status (transport / auth issue, abort the rest)"
    echo
    echo "Result: $PASS/5 passed (early abort)"
    exit 1
fi

# ── Case 2: cmd round trip ──────────────────────────────────────────
bold "Case 2 — wlb cmd \"ver\""
NEEDLE="$RANDOM-$$-wlb-cmd-needle"
OUT="$(run_wlb cmd "echo $NEEDLE" 2>&1 || true)"
echo "$OUT"
if grep -q "$NEEDLE" <<<"$OUT"; then
    pass "2 — cmd echo round-trip"
else
    miss "2 — cmd echo round-trip (needle not in output)"
fi

# ── Case 3: powershell round trip ───────────────────────────────────
bold "Case 3 — wlb powershell"
NEEDLE="$RANDOM-$$-wlb-pwsh-needle"
OUT="$(run_wlb powershell "Write-Output '$NEEDLE'" 2>&1 || true)"
echo "$OUT"
if grep -q "$NEEDLE" <<<"$OUT"; then
    pass "3 — powershell echo round-trip"
else
    miss "3 — powershell echo round-trip (powershell.exe missing? check pwsh / powershell.exe on Windows PATH)"
fi

# ── Case 4: filesync round trip ─────────────────────────────────────
bold "Case 4 — wlb fs push + pull"
SRC="$TMP_DIR/src.bin"
REMOTE_DIR="${WIN_STAGE_DIR:-C:\\Users\\$WIN_USER\\wlb-stage}"
REMOTE_PATH="$REMOTE_DIR\\wlb-roundtrip-$TS.bin"
DST="$TMP_DIR/dst.bin"
head -c 4096 /dev/urandom > "$SRC"
SRC_SUM="$(sha256sum "$SRC" | awk '{print $1}')"

echo "  src    : $SRC ($SRC_SUM)"
echo "  remote : $REMOTE_PATH"

# Ensure remote dir exists.
if ! run_wlb cmd "if not exist \"$REMOTE_DIR\" mkdir \"$REMOTE_DIR\""; then
    miss "4 — could not create $REMOTE_DIR on Windows"
elif ! run_wlb fs push "$SRC" "$REMOTE_PATH"; then
    miss "4 — push failed"
elif ! run_wlb fs pull "$REMOTE_PATH" "$DST"; then
    miss "4 — pull failed"
else
    DST_SUM="$(sha256sum "$DST" | awk '{print $1}')"
    if [[ "$SRC_SUM" == "$DST_SUM" ]]; then
        pass "4 — push/pull round-trip (sha256 matches)"
    else
        miss "4 — push/pull round-trip (sha256 differs: $SRC_SUM vs $DST_SUM)"
    fi
    run_wlb cmd "del \"$REMOTE_PATH\"" >/dev/null 2>&1 || true
fi

# ── Case 5: tool runner ─────────────────────────────────────────────
bold "Case 5 — wlb tool (declarative)"
TOOLS_FILE="$TMP_DIR/wlb-tools.toml"
cat > "$TOOLS_FILE" <<EOF
# Minimal walkthrough tool: a no-op that echoes a known token via cmd.
[tools.walkthrough_echo]
interpreter      = "cmd"
description      = "Smoke-test echo for the walkthrough"
command_template = "echo walkthrough-tool-{tag}"
args             = ["tag"]
timeout          = 10
EOF

export WLB_TOOLS_FILE="$TOOLS_FILE"
if ! run_wlb tool list | grep -q walkthrough_echo; then
    miss "5 — tool list did not surface walkthrough_echo"
else
    OUT="$(run_wlb tool run walkthrough_echo --arg "tag=$TS" 2>&1 || true)"
    echo "$OUT"
    if grep -q "walkthrough-tool-$TS" <<<"$OUT"; then
        pass "5 — tool run round-trip"
    else
        miss "5 — tool run output missing token"
    fi
fi

echo
echo "Result: $PASS/5 passed"
} | tee "$LOG_FILE"

# Exit code reflects pass/fail.
if [[ "$FAIL" -eq 0 ]]; then
    echo "log: $LOG_FILE"
    exit 0
else
    echo "log: $LOG_FILE"
    exit 1
fi
