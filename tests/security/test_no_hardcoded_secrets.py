"""Clase NIST (Protect): ninguna credencial hardcodeada en el repositorio.

Revisa TODOS los archivos versionados (`git ls-files`), así que cubre también
los que se añadan mañana. Tres reglas:

1. **Forma de credencial** en cualquier archivo de texto: claves `sk-…` (proxy
   LiteLLM/OpenAI), `re_…` (Resend), JWT completos (`eyJ….eyJ…`, como las
   claves de Supabase), claves AWS, tokens de GitHub, bloques de clave privada
   y URLs de Postgres/Redis con la contraseña dentro.
2. **Asignación literal** en el código fuente (fuera de los tests): una
   constante con nombre de secreto (`*_SECRET*`, `*API_KEY*`, `*PASSWORD*`…)
   igualada a un texto fijo. Los secretos llegan por variables de entorno.
3. **Archivos `.env`**: solo se versionan los `.example`, y en ellos los
   valores sensibles están vacíos o son plantillas evidentes.

Si este test falla con un secreto real: retíralo del archivo, regenéralo en
el proveedor (ya quedó en el historial de git) y léelo con `os.getenv`.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Formas de credencial reales. Cada una con un nombre para que el fallo diga qué es.
TOKEN_SHAPES: Dict[str, re.Pattern] = {
    "llm_api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "resend_api_key": re.compile(r"\bre_[A-Za-z0-9]{20,}"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Contraseña dentro de la URL, salvo plantillas: [PASSWORD], <password>, ${VAR}, xxxx.
    "db_url_with_password": re.compile(r"\b(?:postgres(?:ql)?(?:\+\w+)?|redis)://[^:/\s]*:(?![\[<$]|x{3,})[^@\s]{4,}@"),
}

# Constante con nombre de secreto = "texto fijo". Distingue mayúsculas a
# propósito: `errors.password = "La contraseña es obligatoria"` no es un secreto.
SECRET_NAME = r"[A-Z0-9_]*(?:SECRET|API_KEY|APIKEY|PASSWORD|PASSWD|PRIVATE_KEY|ACCESS_TOKEN)[A-Z0-9_]*"
LITERAL_ASSIGNMENT = re.compile(
    rf"\b(?:{SECRET_NAME}|apiKey|clientSecret|secretKey)\b\s*[:=]\s*['\"](?P<value>[^'\"$<{{]{{8,}})['\"]"
)

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".sh", ".yml", ".yaml", ".toml"}
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|\.test\.[jt]sx?$")
SKIPPED_FILES = {"package-lock.json", "uv.lock"}

# Excepciones revisadas a mano, cada una con su motivo. No añadir una sin él.
ALLOWED_LITERALS: Dict[Tuple[str, str], str] = {
    ("infra/hardening/run-lab.sh", "LAB_ROOT_PASSWORD"): (
        "contraseña sintética del laboratorio de endurecimiento (OWASP): el lab demuestra "
        "justo que el login de root con contraseña queda rechazado tras harden.sh"
    ),
}

PLACEHOLDER = re.compile(r"^$|change-this|your-|xxxx|\[[A-Z_]+\]|<[^>]+>|example", re.IGNORECASE)
SENSITIVE_ENV_NAME = re.compile(r"SECRET|PASSWORD|API_KEY|_TOKEN$|DATABASE_URL|CLIENT_SECRET")


def tracked_files() -> List[str]:
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git no disponible: este test necesita el repositorio")
    return [path for path in output.split("\0") if path]


def read_text(path: str) -> str | None:
    if Path(path).name in SKIPPED_FILES:
        return None
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None  # binarios (imágenes, PDF) o borrado sin commitear


def token_shape_findings(path: str, text: str) -> Iterator[str]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        for name, pattern in TOKEN_SHAPES.items():
            if pattern.search(line):
                yield f"{path}:{line_no} parece un/a {name}"


def literal_assignment_findings(path: str, text: str) -> Iterator[str]:
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in LITERAL_ASSIGNMENT.finditer(line):
            name = re.match(r"\w+", match.group(0)).group(0)  # type: ignore[union-attr]
            if (path, name) in ALLOWED_LITERALS:
                continue
            yield f"{path}:{line_no} asigna un texto fijo a {name}"


# --- El detector detecta (las muestras se montan aquí, nunca aparecen enteras) ----


@pytest.mark.parametrize(
    "sample,expected",
    [
        ("LLM_API_KEY=" + "sk-" + "A1b2C3d4E5f6G7h8I9j0K1", "llm_api_key"),
        ("key = '" + "re_" + "ABCdef123456ghiJKL7890" + "'", "resend_api_key"),
        ("eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJ" + "zdWIiOiIxMjM0In0" + "." + "abcdefghijKLMNOP", "jwt"),
        ("-----BEGIN " + "RSA PRIVATE KEY-----", "private_key_block"),
        ("postgresql://postgres.ref:" + "Sup3rS3cret" + "@pooler:6543/postgres", "db_url_with_password"),
    ],
)
def test_detector_catches_real_credential_shapes(sample: str, expected: str) -> None:
    assert TOKEN_SHAPES[expected].search(sample)


@pytest.mark.parametrize(
    "placeholder",
    [
        "postgresql://postgres.xxxx:[PASSWORD]@aws-0-region.pooler.supabase.com:6543/postgres",
        "postgresql://user:<password>@localhost/db",
        "redis://:${REDIS_PASSWORD}@redis:6379/0",
        "redis://localhost:6379/0",
    ],
)
def test_detector_ignores_placeholders_and_passwordless_urls(placeholder: str) -> None:
    assert not TOKEN_SHAPES["db_url_with_password"].search(placeholder)


def test_detector_catches_literal_secret_assignments() -> None:
    sample = 'JWT_SECRET_KEY = "' + "una-clave-de-verdad" + '"'
    assert list(literal_assignment_findings("services/api/x.py", sample))
    assert not list(literal_assignment_findings("x.tsx", 'errors.password = "La contraseña es obligatoria."'))
    assert not list(literal_assignment_findings("x.py", 'JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")'))


# --- El repositorio ----------------------------------------------------------------


def test_no_credential_shapes_in_any_tracked_file() -> None:
    findings = []
    for path in tracked_files():
        text = read_text(path)
        if text is not None:
            findings.extend(token_shape_findings(path, text))
    assert findings == []


def test_no_literal_secrets_in_source_code() -> None:
    findings = []
    for path in tracked_files():
        if Path(path).suffix not in SOURCE_SUFFIXES or TEST_PATH.search(path):
            continue
        text = read_text(path)
        if text is not None:
            findings.extend(literal_assignment_findings(path, text))
    assert findings == []


def test_allowed_literals_still_exist() -> None:
    """Una excepción que ya no corresponde a nada se borra: no se acumulan."""
    for (path, name), reason in ALLOWED_LITERALS.items():
        assert reason
        text = read_text(path) or ""
        assert name in text, f"{path} ya no define {name}: retira la excepción"


def test_only_example_env_files_are_tracked() -> None:
    env_files = [p for p in tracked_files() if Path(p).name.startswith(".env")]
    assert env_files, "se esperaban los .env.example del proyecto"
    assert [p for p in env_files if not p.endswith(".example")] == []


def test_env_examples_hold_only_placeholders_for_sensitive_values() -> None:
    findings = []
    for path in (p for p in tracked_files() if Path(p).name.startswith(".env")):
        for line_no, line in enumerate((read_text(path) or "").splitlines(), start=1):
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = (part.strip() for part in line.split("=", 1))
            if SENSITIVE_ENV_NAME.search(name) and not PLACEHOLDER.search(value):
                findings.append(f"{path}:{line_no} {name} tiene un valor que no parece plantilla")
    assert findings == []


def test_real_env_files_are_ignored_by_git() -> None:
    for path in (".env", "services/api/.env", "mcps/healthcore/.env", "uis/backoffice/.env.local"):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
        assert result.returncode == 0, f"{path} no está en .gitignore"
