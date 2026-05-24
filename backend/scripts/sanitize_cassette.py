"""Sanitiza cassettes VCR (.yaml) reemplazando datos personales del response body.

Uso:
    uv run python -m scripts.sanitize_cassette tests/fixtures/cassettes/flex/*.yaml

Sanitiza el cuerpo de los responses (donde esta el XML del Flex con account IDs reales).
NO toca headers (ya filtrados por vcr_config).
"""
import sys
from pathlib import Path

from scripts.sanitize_xml import sanitize_text


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"Usage: {argv[0]} CASSETTE.yaml [CASSETTE2.yaml ...]", file=sys.stderr)
        return 1

    for path_str in argv[1:]:
        path = Path(path_str)
        if not path.exists():
            print(f"skip: {path} (not found)", file=sys.stderr)
            continue
        content = path.read_text(encoding="utf-8")
        sanitized, stats = sanitize_text(content)
        path.write_text(sanitized, encoding="utf-8")
        print(f"sanitized {path}: {sum(stats.values())} replacements")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
