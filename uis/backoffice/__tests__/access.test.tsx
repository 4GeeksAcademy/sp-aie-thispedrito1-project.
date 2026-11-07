/**
 * Roles y Permisos en el backoffice: lógica pura (types/access.ts, menú,
 * mensajes de 403) y las dos pantallas nuevas.
 *
 * Sin @testing-library: `act` + `createRoot` en jsdom, como el resto de
 * tests de pantalla. Se sustituyen /auth/me (authApi.getMe) y las llamadas
 * de accessApi; la lógica de permisos es la real.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import AdminAccessPage from "../app/admin/access/page";
import InternalReportsPage from "../app/internal-reports/page";
import { resetAccessProfileCache } from "../hooks/useAccessProfile";
import { accessApi, internalReportsApi } from "../services/accessApi";
import { authApi } from "../services/authApi";
import { ApiRequestError, requestJson } from "../services/http";
import {
  DEPARTMENT_LABELS,
  FORBIDDEN_MESSAGES,
  ROLE_LABELS,
  can,
  isAdmin,
  scopeDescription,
  type AccessProfile,
  type Capability,
  type InternalReport,
} from "../types/access";
import { NAV_SECTIONS, visibleSections } from "../types/navigation";

jest.mock("../services/authApi", () => ({ authApi: { getMe: jest.fn() } }));
jest.mock("../services/accessApi", () => ({
  accessApi: { listRoles: jest.fn(), listDepartments: jest.fn(), listUsers: jest.fn(), assign: jest.fn() },
  internalReportsApi: { list: jest.fn(), create: jest.fn(), update: jest.fn(), approve: jest.fn(), remove: jest.fn() },
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const EMPLOYEE_CAPS: Capability[] = ["records:read", "records:create"];
const SUPERVISOR_CAPS: Capability[] = [...EMPLOYEE_CAPS, "records:update", "records:approve"];
const ADMIN_CAPS: Capability[] = [
  ...SUPERVISOR_CAPS,
  "records:delete",
  "users:manage",
  "access:manage",
  "departments:read_all",
];

function profile(role: string, department: string | null, capabilities: Capability[]): AccessProfile {
  return { role, role_rank: capabilities.length, department, department_id: department ? `id-${department}` : null, capabilities };
}

const PRIYA = profile("employee", "patient_experience", EMPLOYEE_CAPS);
const MARCUS = profile("supervisor", "clinical_operations", SUPERVISOR_CAPS);
const JAMES = profile("admin", "technology", ADMIN_CAPS);

const mockedGetMe = authApi.getMe as jest.Mock;

function mockMe(value: AccessProfile) {
  mockedGetMe.mockResolvedValue({ id: "u1", email: "x@healthcore.example", profile: {}, ...value });
}

let container: HTMLDivElement;
let root: Root;

async function render(element: React.ReactElement) {
  await act(async () => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    root.render(element);
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function buttonTexts(): string[] {
  return Array.from(container.querySelectorAll("button")).map((button) => button.textContent ?? "");
}

beforeEach(() => {
  jest.clearAllMocks();
  resetAccessProfileCache();
  window.localStorage.setItem("healthcore.auth.token", `token-${Math.random()}`);
});

afterEach(() => {
  act(() => root?.unmount());
  container?.remove();
});

describe("lógica de permisos", () => {
  test("can() solo concede lo que el rol trae; sin perfil, nada", () => {
    expect(can(PRIYA, "records:create")).toBe(true);
    expect(can(PRIYA, "records:update")).toBe(false);
    expect(can(MARCUS, "records:approve")).toBe(true);
    expect(can(MARCUS, "records:delete")).toBe(false);
    expect(can(null, "records:read")).toBe(false);
    expect(isAdmin(JAMES)).toBe(true);
    expect(isAdmin(MARCUS)).toBe(false);
  });

  test("las etiquetas cubren exactamente los códigos del BRIEFING", () => {
    expect(Object.keys(ROLE_LABELS)).toEqual(["employee", "supervisor", "admin"]);
    expect(Object.keys(DEPARTMENT_LABELS).sort()).toEqual(
      ["clinical_operations", "compliance", "patient_experience", "people_workforce", "revenue_cycle", "technology"],
    );
  });

  test("la entrada de administración solo aparece para Admin", () => {
    const hrefs = (value: AccessProfile | null) =>
      visibleSections(NAV_SECTIONS, value).flatMap((section) => section.items.map((item) => item.href));

    expect(hrefs(JAMES)).toContain("/admin/access");
    expect(hrefs(MARCUS)).not.toContain("/admin/access");
    expect(hrefs(null)).not.toContain("/admin/access");
    expect(hrefs(PRIYA)).toContain("/internal-reports");
    // Una sección que se queda vacía desaparece entera.
    expect(visibleSections(NAV_SECTIONS, PRIYA).map((section) => section.title)).not.toContain("Administración");
  });

  test("scopeDescription explica qué departamento ve cada uno", () => {
    expect(scopeDescription(MARCUS)).toContain("Operaciones clínicas");
    expect(scopeDescription(JAMES)).toContain("todos los departamentos");
    expect(scopeDescription(profile("employee", null, EMPLOYEE_CAPS))).toContain("no tiene departamento");
  });
});

describe("mensajes de 403", () => {
  const realFetch = global.fetch;
  afterEach(() => {
    global.fetch = realFetch;
  });

  test.each(Object.entries(FORBIDDEN_MESSAGES))("el código %s se muestra en español", async (code, message) => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 403,
      json: async () => ({ detail: { code, message: "English message" } }),
    }) as unknown as typeof fetch;

    const error = await requestJson("/internal-reports").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiRequestError);
    expect((error as ApiRequestError).status).toBe(403);
    expect((error as ApiRequestError).message).toBe(message);
  });
});

describe("pantalla /admin/access", () => {
  test("un Supervisor ve un 403 y la pantalla no pide nada a la API de administración", async () => {
    mockMe(MARCUS);
    await render(<AdminAccessPage />);

    expect(container.textContent).toContain("Acceso denegado (403)");
    expect(container.textContent).toContain("Supervisor");
    expect(accessApi.listUsers).not.toHaveBeenCalled();
    expect(accessApi.listRoles).not.toHaveBeenCalled();
  });

  test("un Admin ve usuarios con dos controles separados: rol y departamento", async () => {
    mockMe(JAMES);
    (accessApi.listRoles as jest.Mock).mockResolvedValue([
      { id: "r1", code: "employee", name: "Employee", rank: 1, capabilities: EMPLOYEE_CAPS },
      { id: "r2", code: "supervisor", name: "Supervisor", rank: 2, capabilities: SUPERVISOR_CAPS },
    ]);
    (accessApi.listDepartments as jest.Mock).mockResolvedValue([
      { id: "d1", code: "revenue_cycle", name: "Revenue Cycle and Billing" },
      { id: "d2", code: "compliance", name: "Compliance and Data Governance" },
    ]);
    (accessApi.listUsers as jest.Mock).mockResolvedValue([
      { id: "tom", name: "Tom Callahan", role: "supervisor", role_id: "r2", department: "revenue_cycle", department_id: "d1", is_active: true, created_at: "2026-11-07T00:00:00Z" },
    ]);
    (accessApi.assign as jest.Mock).mockResolvedValue({});
    await render(<AdminAccessPage />);

    const departmentSelect = container.querySelector('select[aria-label="Departamento de Tom Callahan"]') as HTMLSelectElement;
    expect(container.querySelector('select[aria-label="Rol de Tom Callahan"]')).not.toBeNull();

    await act(async () => {
      departmentSelect.value = "d2";
      departmentSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });

    // Solo viaja el departamento: el rol no se toca.
    expect(accessApi.assign).toHaveBeenCalledWith("tom", { department_id: "d2" });
  });
});

describe("pantalla /internal-reports", () => {
  const REPORT: InternalReport = {
    id: "rep1",
    department_id: "id-clinical_operations",
    department: "clinical_operations",
    title: "Austin North — documentation time spike",
    body: "Up 12% week over week.",
    created_by: "marcus",
    created_at: "2026-11-07T10:00:00Z",
    approved_by: null,
    approved_at: null,
  };

  test("Empleado: crea, pero no ve Editar, Aprobar ni Eliminar", async () => {
    mockMe(PRIYA);
    (internalReportsApi.list as jest.Mock).mockResolvedValue([{ ...REPORT, department: "patient_experience" }]);
    await render(<InternalReportsPage />);

    expect(container.textContent).toContain("Experiencia y acceso del paciente");
    expect(buttonTexts()).toEqual(expect.arrayContaining(["Crear en Experiencia y acceso del paciente"]));
    expect(buttonTexts()).not.toEqual(expect.arrayContaining(["Editar"]));
    expect(buttonTexts()).not.toEqual(expect.arrayContaining(["Aprobar"]));
    expect(buttonTexts()).not.toEqual(expect.arrayContaining(["Eliminar"]));
  });

  test("Supervisor: edita y aprueba, pero no elimina", async () => {
    mockMe(MARCUS);
    (internalReportsApi.list as jest.Mock).mockResolvedValue([REPORT]);
    await render(<InternalReportsPage />);

    expect(buttonTexts()).toEqual(expect.arrayContaining(["Editar", "Aprobar"]));
    expect(buttonTexts()).not.toEqual(expect.arrayContaining(["Eliminar"]));
  });

  test("Admin: también elimina", async () => {
    mockMe(JAMES);
    (internalReportsApi.list as jest.Mock).mockResolvedValue([REPORT]);
    await render(<InternalReportsPage />);

    expect(buttonTexts()).toEqual(expect.arrayContaining(["Editar", "Aprobar", "Eliminar"]));
    expect(container.textContent).toContain("todos los departamentos");
  });

  test("un 403 al cargar muestra el motivo, no un error de red", async () => {
    mockMe(profile("employee", null, EMPLOYEE_CAPS));
    (internalReportsApi.list as jest.Mock).mockRejectedValue(
      new ApiRequestError(FORBIDDEN_MESSAGES.department_required, 403),
    );
    await render(<InternalReportsPage />);

    expect(container.textContent).toContain(FORBIDDEN_MESSAGES.department_required);
    expect(container.textContent).not.toContain("Verifica que la API esté activa");
  });
});
