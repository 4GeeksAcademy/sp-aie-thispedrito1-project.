"""Tabla `audit_log_entries`: append-only por diseño, no por convención.

Tres capas, de fuera hacia dentro:

1. **API**: el router solo expone GET. No existe ninguna ruta que edite o
   borre una entrada (hay un test que recorre `app.routes`).
2. **Aplicación (ORM)**: `before_update`/`before_delete` del mapper lanzan
   `AuditLogImmutableError` si algún código intenta modificar o borrar una
   entrada cargada en sesión.
3. **Base de datos**: triggers que rechazan `UPDATE`, `DELETE` y `TRUNCATE`
   (Postgres) o `UPDATE`/`DELETE` (SQLite de los tests). Cubren también el
   SQL masivo (`session.execute(update(...))`), que no pasa por el mapper.

En Supabase se activa además RLS sin políticas: los roles de la API pública
de Supabase (`anon`, `authenticated`) no pueden ni leer la tabla. El dueño
(el usuario `postgres` con el que se conecta la API) sí podría desactivar el
trigger: por eso existe la cadena de hashes (`chain.py`), que deja a la vista
cualquier alteración hecha por ese camino.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, Index, Integer, UniqueConstraint, event
from sqlmodel import Field, SQLModel

TABLE_NAME = "audit_log_entries"


class AuditLogImmutableError(RuntimeError):
    """Se intentó modificar o borrar una entrada ya escrita."""


class AuditLogEntry(SQLModel, table=True):
    __tablename__ = TABLE_NAME
    __table_args__ = (
        UniqueConstraint("entry_id", name="uq_audit_log_entry_id"),
        # Cada eslabón tiene como mucho un sucesor: dos escrituras que leyeran
        # el mismo "último" a la vez no pueden bifurcar la cadena.
        UniqueConstraint("prev_hash", name="uq_audit_log_prev_hash"),
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_actor", "actor_type", "actor_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_patient_ref", "patient_ref"),
        Index("ix_audit_log_department", "department_id"),
    )

    # Posición en la cadena (1, 2, 3…). La asigna `writer.record`, no la base
    # de datos: forma parte de lo que se firma con el hash.
    sequence: int = Field(sa_column=Column(Integer, primary_key=True, autoincrement=False))
    entry_id: str = Field(max_length=36)
    # Texto ISO-8601 de ancho fijo en UTC ("2026-11-07T10:15:00.000000Z"):
    # se compara bien como texto y el hash se recalcula byte a byte igual en
    # Postgres y en SQLite (un DateTime vuelve con o sin zona según el motor).
    occurred_at: str = Field(max_length=27)
    actor_type: str = Field(max_length=16)
    # Persona: id interno de la cuenta. Proceso: su nombre. Nunca un email.
    actor_id: Optional[str] = Field(default=None, max_length=64)
    # Proceso que actúa a petición de una persona: quién lo pidió.
    on_behalf_of: Optional[str] = Field(default=None, max_length=64)
    action: str = Field(max_length=48)
    resource_type: str = Field(max_length=32)
    resource_id: Optional[str] = Field(default=None, max_length=64)
    # Identificador OPACO del paciente (BRIEFING §4). Nunca nombre ni historia.
    patient_ref: Optional[str] = Field(default=None, max_length=16)
    # Departamento al que pertenece la entrada: decide qué Supervisor la ve.
    department_id: Optional[str] = Field(default=None, max_length=36)
    # Motivo del acceso (código de `catalog.ACCESS_PURPOSES`).
    context: Optional[str] = Field(default=None, max_length=40)
    # "ip:203.0.113.7" o "process:<nombre>:pid<n>".
    origin: str = Field(max_length=80)
    outcome: str = Field(max_length=16)
    # JSON canónico (claves ordenadas). Se guarda como texto para que el hash
    # se pueda recalcular exactamente.
    details: str = Field(default="{}")
    prev_hash: str = Field(max_length=64)
    entry_hash: str = Field(max_length=64)


# --- Capa 2: el ORM no deja modificar ni borrar ---------------------------------


@event.listens_for(AuditLogEntry, "before_update")
def _refuse_update(_mapper, _connection, target: AuditLogEntry) -> None:
    raise AuditLogImmutableError(f"Audit entry {target.sequence} is append-only: UPDATE refused.")


@event.listens_for(AuditLogEntry, "before_delete")
def _refuse_delete(_mapper, _connection, target: AuditLogEntry) -> None:
    raise AuditLogImmutableError(f"Audit entry {target.sequence} is append-only: DELETE refused.")


# --- Capa 3: la base de datos no deja modificar ni borrar --------------------------

_POSTGRES_GUARDS = (
    # Sin '%' en el cuerpo: psycopg2 lo interpretaría como marcador.
    f"""
    CREATE OR REPLACE FUNCTION {TABLE_NAME}_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION USING
            MESSAGE = '{TABLE_NAME} is append-only: ' || TG_OP || ' refused',
            ERRCODE = 'insufficient_privilege';
    END;
    $$
    """,
    f"DROP TRIGGER IF EXISTS {TABLE_NAME}_no_update_delete ON {TABLE_NAME}",
    f"""
    CREATE TRIGGER {TABLE_NAME}_no_update_delete
    BEFORE UPDATE OR DELETE ON {TABLE_NAME}
    FOR EACH ROW EXECUTE FUNCTION {TABLE_NAME}_append_only()
    """,
    f"DROP TRIGGER IF EXISTS {TABLE_NAME}_no_truncate ON {TABLE_NAME}",
    f"""
    CREATE TRIGGER {TABLE_NAME}_no_truncate
    BEFORE TRUNCATE ON {TABLE_NAME}
    FOR EACH STATEMENT EXECUTE FUNCTION {TABLE_NAME}_append_only()
    """,
    # Supabase expone las tablas de `public` por su API REST a los roles
    # anon/authenticated. RLS sin políticas = esos roles no ven nada.
    f"ALTER TABLE {TABLE_NAME} ENABLE ROW LEVEL SECURITY",
    f"""
    DO $$
    DECLARE r text;
    BEGIN
        FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE 'REVOKE ALL ON {TABLE_NAME} FROM ' || quote_ident(r);
            END IF;
        END LOOP;
    END;
    $$
    """,
)

_SQLITE_GUARDS = (
    f"""
    CREATE TRIGGER IF NOT EXISTS {TABLE_NAME}_no_update
    BEFORE UPDATE ON {TABLE_NAME}
    BEGIN SELECT RAISE(ABORT, '{TABLE_NAME} is append-only: UPDATE refused'); END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS {TABLE_NAME}_no_delete
    BEFORE DELETE ON {TABLE_NAME}
    BEGIN SELECT RAISE(ABORT, '{TABLE_NAME} is append-only: DELETE refused'); END
    """,
)


def install_append_only_guards(connection) -> None:
    """Instala (o reinstala) los triggers. Idempotente."""
    dialect = connection.dialect.name
    statements = _POSTGRES_GUARDS if dialect == "postgresql" else _SQLITE_GUARDS if dialect == "sqlite" else ()
    for statement in statements:
        connection.exec_driver_sql(statement)


@event.listens_for(AuditLogEntry.__table__, "after_create")
def _guards_on_create(_table, connection, **_kw) -> None:
    install_append_only_guards(connection)


def ensure_audit_log_guards(engine) -> None:
    """Para el arranque de la API: `create_all` no vuelve a disparar
    `after_create` si la tabla ya existía, así que los triggers se aseguran
    aparte (por si una base antigua se creó sin ellos)."""
    with engine.begin() as connection:
        install_append_only_guards(connection)
