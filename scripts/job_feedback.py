#!/usr/bin/env python3
"""Record and report manual relevance feedback for recommended jobs."""

import argparse
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from job_radar.domain import (  # noqa: E402
    FeedbackReport,
    PendingFeedback,
    RecordedFeedback,
    Relevance,
    RelevanceStats,
)
from job_radar.storage import FeedbackError, FeedbackRepository  # noqa: E402


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("debe ser un entero positivo")
    return number


def _rate(stats: RelevanceStats) -> str:
    if stats.relevance_rate is None:
        return "--"
    return f"{stats.relevance_rate:.1%}"


def _format_recorded(recorded: RecordedFeedback) -> str:
    feedback = recorded.feedback
    previous = recorded.previous_relevance

    if previous is None:
        action = "CREATED"
    elif previous == feedback.relevance:
        action = "UNCHANGED"
    else:
        action = f"UPDATED from={previous}"

    return (
        f"{action} relevance={feedback.relevance} "
        f"source={feedback.source} job_id={feedback.source_job_id} "
        f"rules_version={feedback.match_rules_version} "
        f"status={feedback.match_status!r} "
        f"reason_codes={','.join(feedback.match_reason_codes) or '-'} "
        f"job={recorded.company!r}/{recorded.title!r}"
    )


def _format_pending(pending: PendingFeedback) -> str:
    return (
        f"{pending.source:<18} {pending.source_job_id:<24} "
        f"v{pending.match_rules_version:<3} {pending.match_status:<20} "
        f"{pending.company} — {pending.title}  {pending.url}"
    )


def _format_breakdown(
    title: str,
    rows: tuple[RelevanceStats, ...],
    *,
    prefix: str = "",
) -> list[str]:
    lines = [
        "",
        title,
        f"  {'KEY':<28} {'LABELED':>7} {'RELEVANT':>8} "
        f"{'NOT_RELEVANT':>12} {'RATE':>7}",
    ]
    lines.extend(
        f"  {prefix + stats.key:<28} {stats.labeled:>7} "
        f"{stats.relevant:>8} {stats.not_relevant:>12} {_rate(stats):>7}"
        for stats in rows
    )
    return lines


def _format_report(
    report: FeedbackReport,
    rules_version: int | None,
) -> str:
    overall = report.overall
    scope = (
        f" (rules_version={rules_version})"
        if rules_version is not None
        else ""
    )
    lines = [
        "Precisión de recomendaciones mostradas; no mide recall."
        + scope,
        "",
        f"TOTAL LABELED   {overall.labeled}",
        f"RELEVANT        {overall.relevant}",
        f"NOT_RELEVANT    {overall.not_relevant}",
        f"RELEVANCE RATE  {_rate(overall)} "
        f"({overall.relevant}/{overall.labeled})",
    ]

    if overall.labeled == 0:
        lines.extend(["", "Sin feedback registrado."])
        return "\n".join(lines)

    lines.extend(
        _format_breakdown(
            "By rules version",
            report.by_rules_version,
            prefix="v",
        )
    )
    lines.extend(
        _format_breakdown("By match status", report.by_match_status)
    )
    lines.extend(
        _format_breakdown(
            "By reason code (más falsos positivos primero)",
            report.by_reason_code,
        )
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    rate = commands.add_parser(
        "rate",
        help="registra o cambia la valoración de una recomendación",
    )
    rate.add_argument("--source", required=True)
    rate.add_argument("--job-id", required=True)
    rate.add_argument(
        "--relevance",
        required=True,
        choices=[relevance.value for relevance in Relevance],
    )

    pending = commands.add_parser(
        "pending",
        help="lista recomendaciones activas sin feedback para su versión",
    )
    pending.add_argument("--limit", type=_positive_int, default=20)

    report = commands.add_parser(
        "report",
        help="muestra la tasa de relevancia y sus desgloses",
    )
    report.add_argument(
        "--rules-version",
        type=_positive_int,
        help="limita el informe a una versión de MATCH_RULES_VERSION",
    )
    args = parser.parse_args(argv)

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        parser.error("Falta DATABASE_URL")

    repository = FeedbackRepository(database_url)

    if args.command == "rate":
        try:
            recorded = repository.record(
                source=args.source,
                source_job_id=args.job_id,
                relevance=Relevance(args.relevance),
            )
        except FeedbackError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 1

        print(_format_recorded(recorded))
        return 0

    if args.command == "pending":
        rows = repository.get_pending(limit=args.limit)
        for row in rows:
            print(_format_pending(row))
        print(f"Pendientes mostradas: {len(rows)}")
        return 0

    print(
        _format_report(
            repository.get_report(rules_version=args.rules_version),
            args.rules_version,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
