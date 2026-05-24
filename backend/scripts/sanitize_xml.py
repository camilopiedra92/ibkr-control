"""Sanitiza un XML del Flex reemplazando datos personales por placeholders.

Uso:
    uv run python -m scripts.sanitize_xml INPUT.xml OUTPUT.xml

Idempotente: si vuelve a correr sobre el output, no cambia nada.
"""
import re
import sys
from pathlib import Path


# Mapping de reemplazos: regex pattern -> replacement
# Account IDs reales -> placeholders
REPLACEMENTS = [
    (re.compile(r"U99999001"), "U99999001"),
    (re.compile(r"U99999002"), "U99999002"),
    (re.compile(r"U99999003"), "U99999003"),
    # NIT colombiano del titular
    (re.compile(r"1234567890"), "1234567890"),
]


def sanitize_text(content: str) -> tuple[str, dict[str, int]]:
    """Aplica REPLACEMENTS al texto. Retorna (texto, conteo_por_pattern)."""
    stats: dict[str, int] = {}
    for pattern, replacement in REPLACEMENTS:
        new_content, n = pattern.subn(replacement, content)
        if n > 0:
            stats[pattern.pattern] = n
        content = new_content
    return content, stats


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"Usage: {argv[0]} INPUT.xml OUTPUT.xml", file=sys.stderr)
        return 1

    input_path = Path(argv[1])
    output_path = Path(argv[2])

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2

    content = input_path.read_text(encoding="utf-8")
    sanitized, stats = sanitize_text(content)
    output_path.write_text(sanitized, encoding="utf-8")

    print(f"Sanitized {input_path} -> {output_path}")
    for pattern, n in stats.items():
        print(f"  {pattern}: {n} replacements")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
