"""Contratos de serializacion de la API (docs/serialization-audit.md).

Estos tests no comprueban logica de negocio: comprueban la FORMA de las
respuestas. Existen para que un cambio futuro en un modelo no vuelva a
filtrar campos por la API sin que nadie se entere — que es exactamente el
fallo que motivo esta auditoria.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app

VALID_SUPPLIER = {
    "name": "Test Medical Corp",
    "country": "USA",
    "categories": ["medical_supplies"],
    "monthly_rate": 1200.0,
    "currency": "USD",
    "status": "active",
    "contact_email": "ventas@testmedical.com",
    "notes": "Nota interna que la tabla de listado no muestra.",
    "compliance_agreement": "BAA",
}

VALID_INCIDENT = {
    "title": "EHR sync failure between London clinics",
    "description": "Referrals stopped syncing between London City and London West End.",
    "category": "it_system",
    "status": "open",
    "origin": "internal",
    "branch": "central",
}

# Nunca deben aparecer en el cuerpo de una respuesta 2xx. `access_token` se
# excluye a proposito de esta lista: es el proposito mismo de /auth/login.
CAMPOS_PROHIBIDOS = {"hashed_password", "password", "new_password", "current_password"}


def _schema_de_respuesta(operacion: dict, schemas: dict) -> dict | None:
    """Devuelve las propiedades del esquema 2xx de una operacion OpenAPI."""
    for codigo, respuesta in (operacion.get("responses") or {}).items():
        if not codigo.startswith("2"):
            continue
        contenido = (respuesta.get("content") or {}).get("application/json") or {}
        esquema = contenido.get("schema") or {}
        ref = esquema.get("$ref") or (esquema.get("items") or {}).get("$ref")
        if ref:
            return (schemas.get(ref.split("/")[-1]) or {}).get("properties") or {}
    return None


# --- Barridos sobre todo el esquema OpenAPI -------------------------------


def test_ninguna_respuesta_expone_credenciales() -> None:
    """Guardian transversal: recorre TODA la superficie de la API, no una
    lista de endpoints escrita a mano, para que un endpoint nuevo tambien
    quede cubierto sin tocar este test."""
    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    filtraciones = []
    for ruta, operaciones in spec["paths"].items():
        for verbo, operacion in operaciones.items():
            props = _schema_de_respuesta(operacion, schemas)
            if props and (set(props) & CAMPOS_PROHIBIDOS):
                filtraciones.append(f"{verbo.upper()} {ruta}")

    assert filtraciones == [], f"Endpoints que exponen credenciales: {filtraciones}"


def test_todo_endpoint_json_declara_su_esquema_de_respuesta() -> None:
    """Requisito minimo del hito: ningun endpoint devuelve JSON sin contrato.

    Se excluyen las respuestas 204 (sin cuerpo por definicion) y las que
    sirven archivos (HTML de la UI estatica, descarga CSV), donde lo correcto
    es declarar el content-type y no un response_model que prometeria JSON.
    """
    spec = app.openapi()
    sin_contrato = []

    for ruta, operaciones in spec["paths"].items():
        for verbo, operacion in operaciones.items():
            respuestas = operacion.get("responses") or {}
            exitosas = {c: r for c, r in respuestas.items() if c.startswith("2")}
            for codigo, respuesta in exitosas.items():
                contenido = respuesta.get("content") or {}
                if not contenido:  # 204 No Content
                    continue
                if "application/json" not in contenido:  # text/html, text/csv
                    continue
                if not (contenido["application/json"].get("schema")):
                    sin_contrato.append(f"{verbo.upper()} {ruta} ({codigo})")

    assert sin_contrato == [], f"Endpoints JSON sin esquema: {sin_contrato}"


def test_los_flujos_de_auth_no_autenticados_no_devuelven_email() -> None:
    """Registro, login, forgot y reset no reenvian el email. /auth/me si
    puede: el llamante esta autenticado y el correo es suyo."""
    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    for ruta in ["/users", "/auth/login", "/auth/token", "/auth/forgot-password", "/auth/reset-password"]:
        operacion = spec["paths"][ruta].get("post")
        if operacion is None:
            continue
        props = _schema_de_respuesta(operacion, schemas) or {}
        assert not any("email" in campo for campo in props), f"POST {ruta} devuelve email: {list(props)}"


# --- Contratos concretos por endpoint -------------------------------------


def test_listado_de_proveedores_omite_campos_que_la_tabla_no_muestra(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    creado = client.post("/suppliers", json=VALID_SUPPLIER, headers=auth_headers)
    assert creado.status_code == 201, creado.text

    listado = client.get("/suppliers", headers=auth_headers)
    assert listado.status_code == 200
    fila = listado.json()[0]

    assert set(fila) == {
        "id", "name", "country", "categories",
        "monthly_rate", "currency", "status", "updated_at",
    }
    for omitido in ("contact_email", "notes", "compliance_agreement", "contract_renewal_date"):
        assert omitido not in fila


def test_el_detalle_de_proveedor_si_devuelve_los_campos_completos(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """El recorte es del LISTADO, no del recurso: el detalle sigue siendo la
    proyeccion completa. Sin esto, "optimizar el payload" seria perdida de
    funcionalidad disfrazada."""
    creado = client.post("/suppliers", json=VALID_SUPPLIER, headers=auth_headers).json()

    detalle = client.get(f"/suppliers/{creado['id']}", headers=auth_headers)
    assert detalle.status_code == 200
    body = detalle.json()
    assert body["contact_email"] == VALID_SUPPLIER["contact_email"]
    assert body["notes"] == VALID_SUPPLIER["notes"]


def test_listado_de_incidencias_omite_updated_at(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    creada = client.post("/api/incidents", json=VALID_INCIDENT, headers=auth_headers)
    assert creada.status_code == 201, creada.text

    fila = client.get("/api/incidents", headers=auth_headers).json()[0]
    assert "updated_at" not in fila
    assert set(fila) == {
        "id", "title", "description", "category",
        "status", "origin", "branch", "created_at",
    }

    # El detalle si lo conserva.
    detalle = client.get(f"/api/incidents/{fila['id']}", headers=auth_headers).json()
    assert "updated_at" in detalle


def test_perfil_propio_solo_devuelve_datos_de_perfil(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    """ProfilePublic omite el id de la fila de perfil y el user_id: ambos son
    detalle de almacenamiento, y /profiles/me esta siempre acotado al
    llamante."""
    response = client.get("/profiles/me", headers=auth_headers)

    assert response.status_code == 200
    assert set(response.json()) == {"name", "phone", "address"}


def test_auth_me_devuelve_email_propio_pero_perfil_aplanado(
    client: TestClient, registered_user: dict[str, str], auth_headers: dict[str, str]
) -> None:
    response = client.get("/auth/me", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == registered_user["email"]   # permitido: es el suyo
    assert "user_id" not in body["profile"]            # redundante con body["id"]
    assert "hashed_password" not in body


def test_health_devuelve_la_forma_declarada(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
