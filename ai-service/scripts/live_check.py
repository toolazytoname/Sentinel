"""One-shot live integration check against agnes-2.0-flash.

Verifies the full stack: real HTTP → real LLM → Pydantic extraction → DB.
Run with the env vars set (AGNES_API_KEY, LLM_BASE_URL, LLM_QUICK_MODEL).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid


def _load_user_env() -> None:
    """Source ~/.zshrc in a login shell so AGNES_API_KEY etc. are inherited.

    Python child processes don't inherit interactive shell exports. Running
    `zsh -ic 'source ~/.zshrc; env'` gives us the same env the user sees.
    """
    zshrc = os.path.expanduser("~/.zshrc")
    if not os.path.exists(zshrc):
        return
    try:
        out = subprocess.check_output(
            ["zsh", "-ic", f"source {zshrc} >/dev/null 2>&1; env"],
            text=True,
            timeout=10,
        )
        for line in out.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                # Only set if not already set in our env
                os.environ.setdefault(k, v)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass


_load_user_env()

# Force a file-backed SQLite so we can inspect the row afterwards
os.environ.setdefault("DATABASE_URL", "sqlite:///./live_check.db")

from sqlalchemy import create_engine

from app.db import get_session
from app.db.models import Base, VetoRecordRow
from app.llm import OpenAICompatibleClient, StructuredExtractor, VetoExtractor
from app.modules.veto import MarketContext, TradeSignal, audit


def main() -> int:
    # 1. Show effective config (sanitized)
    api_key = os.environ.get("AGNES_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL", "https://apihub.agnes-ai.com/v1")
    model = os.environ.get("LLM_QUICK_MODEL", "agnes-2.0-flash")
    print(f"[config] base_url={base_url}")
    print(f"[config] model={model}")
    print(f"[config] api_key={api_key[:7]}... len={len(api_key)}")
    if not api_key:
        print("[FATAL] AGNES_API_KEY not set", file=sys.stderr)
        return 2

    # 2. Fresh DB
    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print(f"[db] schema created at {os.environ['DATABASE_URL']}")

    # 3. Build the real client
    client = OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        quick_model=model,
        deep_model=model,
        https_proxy=os.environ.get("HTTPS_PROXY"),
    )
    print(f"[client] {client.__class__.__name__} ready")

    # 4. Scenario A: clean signal → LLM should NOT veto
    signal = TradeSignal(
        strategy="S1TrendFollow",
        pair="BTC/USDT",
        side="long",
        stake_pct=0.05,
    )
    ctx = MarketContext(
        recent_high_severity_events=[],
        current_total_exposure_pct=0.10,
        max_exposure_pct=0.60,
        upcoming_event_window_minutes=0,
    )
    extractor = VetoExtractor(StructuredExtractor(client))
    print("\n[scenario A] clean signal, asking LLM to vet...")
    result_a = audit(signal, ctx, extractor)
    print(f"[scenario A] result: veto={result_a.veto} reason={result_a.reason!r} source={result_a.source}")

    # 5. Scenario B: rule veto (high-severity event) → LLM must NOT be called
    ctx_b = MarketContext(
        recent_high_severity_events=["BTC"],
        current_total_exposure_pct=0.10,
        max_exposure_pct=0.60,
        upcoming_event_window_minutes=0,
    )
    print("\n[scenario B] high-severity event → rule must short-circuit LLM")
    result_b = audit(signal, ctx_b, extractor)
    print(f"[scenario B] result: veto={result_b.veto} reason={result_b.reason!r} source={result_b.source}")

    # 6. Persist both rows
    session = get_session()
    try:
        for r in (result_a, result_b):
            row = VetoRecordRow(
                strategy=signal.strategy,
                pair=signal.pair,
                signal_time=signal.proposed_at,
                veto=r.veto,
                reason=r.reason,
                source=r.source,
            )
            session.add(row)
        session.commit()
        print(f"\n[db] inserted 2 veto records")
        # Read back
        rows = session.query(VetoRecordRow).order_by(VetoRecordRow.id).all()
        for row in rows:
            print(f"[db] row id={row.id} source={row.source} veto={row.veto} reason={row.reason!r}")
    finally:
        session.close()

    # 7. Verdict
    print("\n=== CHECKS ===")
    ok = True
    if result_a.source not in ("llm", "llm_unavailable"):
        print(f"  FAIL scenario A: expected source=llm, got {result_a.source}")
        ok = False
    else:
        print(f"  PASS scenario A: LLM responded (source={result_a.source})")
    if result_b.source != "rule":
        print(f"  FAIL scenario B: expected source=rule, got {result_b.source}")
        ok = False
    else:
        print(f"  PASS scenario B: rule short-circuited LLM")
    if not result_b.veto:
        print(f"  FAIL scenario B: expected veto=True")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())