#!/usr/bin/env python3
"""Generate the documented source catalog from the connector registry."""

import argparse
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from job_radar.connectors.registry import CONNECTORS  # noqa: E402


BEGIN_MARKER = "<!-- BEGIN GENERATED SOURCE CATALOG -->"
END_MARKER = "<!-- END GENERATED SOURCE CATALOG -->"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _readme_catalog() -> str:
    return (
        f"Job Radar consulta **{len(CONNECTORS)} fuentes empresa/ATS** "
        "configuradas en código."
    )


def _detailed_catalog() -> str:
    lines = [
        f"El registro actual contiene **{len(CONNECTORS)} fuentes**.",
        "",
        "| Empresa | Tipo / `source` | Modo | `catch_all` |",
        "| --- | --- | --- | --- |",
    ]

    for connector in CONNECTORS:
        mode = "required" if connector.required else "opcional"
        catch_all = "sí" if connector.catch_all else "no"
        lines.append(
            "| "
            f"{_markdown_cell(connector.company)} | "
            f"`{_markdown_cell(connector.source)}` | "
            f"{mode} | {catch_all} |"
        )

    return "\n".join(lines)


DOCUMENTS = {
    Path("README.md"): _readme_catalog,
    Path("docs/CONNECTORS.md"): _detailed_catalog,
}


def _replace_generated_block(text: str, generated: str, path: Path) -> str:
    if text.count(BEGIN_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ValueError(
            f"{path} debe contener exactamente un par de marcadores del catálogo"
        )

    begin = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER, begin) + len(END_MARKER)
    replacement = f"{BEGIN_MARKER}\n{generated}\n{END_MARKER}"
    return text[:begin] + replacement + text[end:]


def update_documents(root: Path, check: bool) -> list[Path]:
    changes = []

    for relative_path, render in DOCUMENTS.items():
        path = root / relative_path
        current = path.read_text(encoding="utf-8")
        expected = _replace_generated_block(current, render(), relative_path)

        if current != expected:
            changes.append(relative_path)

            if not check:
                path.write_text(expected, encoding="utf-8")

    return changes


def main(argv: list[str] | None = None, root: Path = REPOSITORY_ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if generated documentation is out of date",
    )
    args = parser.parse_args(argv)

    changes: list[Path] = []
    try:
        changes = update_documents(root, check=args.check)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    if args.check and changes:
        paths = ", ".join(str(path) for path in changes)
        print(f"Catálogo de fuentes desactualizado: {paths}", file=sys.stderr)
        return 1

    if changes:
        paths = ", ".join(str(path) for path in changes)
        print(f"Catálogo de fuentes actualizado: {paths}")
    else:
        print("Catálogo de fuentes actualizado.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
