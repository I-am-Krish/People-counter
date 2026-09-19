#!/usr/bin/env bash
# launch.sh — One-click launcher for Linux & macOS
# Usage: bash launch.sh  OR  chmod +x launch.sh && ./launch.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; NC='\033[0m'

echo ""
echo -e "${CYAN} ╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN} ║          PEOPLE COUNTER — STARTING UP           ║${NC}"
echo -e "${CYAN} ╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ── Check Python ────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}[ERROR]${NC} python3 not found. Install Python 3.10+ first."
    exit 1
fi
PYTHON=python3

# ── Virtual environment ──────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}[SETUP]${NC} Creating virtual environment..."
    $PYTHON -m venv venv
fi

echo -e "${YELLOW}[SETUP]${NC} Activating virtual environment..."
# shellcheck disable=SC1091
source venv/bin/activate

# ── Dependencies ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}[SETUP]${NC} Installing / verifying dependencies..."
pip install -q -r requirements.txt

# ── Analytics server in background ───────────────────────────────────────────
echo ""
echo -e "${GREEN}[INFO]${NC}  Starting Analytics Dashboard on http://127.0.0.1:7003"
python analytics_server.py &
ANALYTICS_PID=$!

sleep 2

# ── Trap SIGINT/SIGTERM so we can clean up both processes ────────────────────
cleanup() {
    echo ""
    echo -e "${YELLOW}[INFO]${NC}  Shutting down..."
    kill "$ANALYTICS_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Counter (foreground) ─────────────────────────────────────────────────────
echo -e "${GREEN}[INFO]${NC}  Starting People Counter stream on http://127.0.0.1:7004"
echo ""
echo -e " ${CYAN}┌─────────────────────────────────────────────────┐${NC}"
echo -e " ${CYAN}│${NC}  Live feed  → ${GREEN}http://127.0.0.1:7004${NC}            ${CYAN}│${NC}"
echo -e " ${CYAN}│${NC}  Dashboard  → ${GREEN}http://127.0.0.1:7003${NC}            ${CYAN}│${NC}"
echo -e " ${CYAN}│${NC}                                                  ${CYAN}│${NC}"
echo -e " ${CYAN}│${NC}  Press ${RED}Ctrl+C${NC} to stop both servers.             ${CYAN}│${NC}"
echo -e " ${CYAN}└─────────────────────────────────────────────────┘${NC}"
echo ""

python people_counter.py
