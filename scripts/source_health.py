#!/usr/bin/env python3
"""Show connector health and check the required-source operational SLO."""

import argparse
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from job_radar.connectors.registry import CONNECTORS  # noqa: E402
from job_radar.observability import (  # noqa: E402
    HEALTH_WINDOW,
    SourceHealth,
    build_source_health,
    required_slo_breaches,
)
from job_radar.storage import RunRepository  # noqa: E402


def _load_health(database_url: str) -> tuple[SourceHealth, ...]:
    repository = RunRepository(database_url)
    histories = repository.get_recent_source_runs(limit=HEALTH_WINDOW)
    return build_source_health(CONNECTORS, histories)


def _percentage(health: SourceHealth) -> str:
    if health.success_rate is None:
        return "  --"
    return f"{health.success_rate:>4.0%}"


def _compact_error(health: SourceHealth) -> str:
    if not health.last_error_type:
        return ""
    message = " ".join((health.last_error_message or "").split())
    detail = (
        f"{health.last_error_type}: {message}"
        if message
        else health.last_error_type
    )
    return f"  last_error={detail[:120]}"


def _date(value) -> str:
    return value.date().isoformat() if value is not None else "never"


def _format_health(health: SourceHealth) -> str:
    mode = "  optional" if not health.required else ""
    failures = (
        f"  consecutive_failures={health.consecutive_failures}"
        if health.consecutive_failures
        else ""
    )
    return (
        f"{health.health_status.upper():<10} "
        f"{health.company:<28} {health.source:<18} "
        f"{health.successful_runs:>2}/{health.recent_runs:<2} "
        f"{_percentage(health)}{mode} "
        f"last_success={_date(health.last_success_at)} "
        f"last_failure={_date(health.last_failure_at)}"
        f"{failures}{_compact_error(health)}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--problems",
        action="store_true",
        help="show only degraded and unhealthy sources",
    )
    modes.add_argument(
        "--check",
        action="store_true",
        help="fail when a required source violates the operational SLO",
    )
    args = parser.parse_args(argv)

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        parser.error("Falta DATABASE_URL")

    health = _load_health(database_url)

    if args.check:
        displayed = required_slo_breaches(health)
    elif args.problems:
        displayed = tuple(
            source
            for source in health
            if source.health_status in {"degraded", "unhealthy"}
        )
    else:
        displayed = health

    for source in displayed:
        print(_format_health(source))

    if args.check:
        if displayed:
            print(
                f"SLO incumplido por {len(displayed)} fuente(s) required.",
                file=sys.stderr,
            )
            return 1
        print("SLO de fuentes required cumplido.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
