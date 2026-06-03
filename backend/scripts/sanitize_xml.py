"""Sanitiza un XML del Flex reemplazando datos personales por placeholders.

Uso:
    uv run python -m scripts.sanitize_xml INPUT.xml OUTPUT.xml

El mapping real -> generico vive en un archivo LOCAL UNTRACKED
(``backend/scripts/.sanitize_mapping.local.json``, gitignored) para que ningun
dato del usuario quede commiteado. Sin ese archivo, el script aborta con un
mensaje claro (no hay reemplazos hardcodeados aca a proposito).

El JSON mapea cualquier substring real a su placeholder: numeros de cuenta,
counterparties, NIT, email, nombre, direccion, fecha de nacimiento, etc.
Las claves que empiezan con ``_`` se ignoran (comentarios).

Idempotente: si vuelve a correr sobre el output, no cambia nada.
"""

import json
import sys
from pathlib import Path

MAPPING_PATH = Path(__file__).parent / ".sanitize_mapping.local.json"


def load_mapping() -> dict[str, str]:
    """Carga el mapping local untracked. Aborta si falta."""
    if not MAPPING_PATH.exists():
        raise SystemExit(
            f"Falta el mapping local: {MAPPING_PATH}\n"
            "Crealo (gitignored) con pares real->generico antes de sanitizar."
        )
    raw = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def sanitize_text(content: str, mapping: dict[str, str]) -> tuple[str, dict[str, int]]:
    """Aplica el mapping al texto (claves mas largas primero para evitar
    reemplazos parciales). Retorna (texto, conteo_por_clave)."""
    stats: dict[str, int] = {}
    for key in sorted(mapping, key=len, reverse=True):
        n = content.count(key)
        if n > 0:
            content = content.replace(key, mapping[key])
            stats[key] = n
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

    mapping = load_mapping()
    content = input_path.read_text(encoding="utf-8")
    sanitized, stats = sanitize_text(content, mapping)
    output_path.write_text(sanitized, encoding="utf-8")

    print(f"Sanitized {input_path} -> {output_path}")
    for key, n in stats.items():
        print(f"  {key!r}: {n} replacements")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
