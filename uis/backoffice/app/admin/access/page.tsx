"use client";

import { useCallback, useState } from "react";

import { AsyncSection } from "../../../components/AsyncSection";
import { useAccessProfile } from "../../../hooks/useAccessProfile";
import { useAsyncData } from "../../../hooks/useAsyncData";
import { accessApi } from "../../../services/accessApi";
import { ApiFieldError } from "../../../services/http";
import {
  departmentLabel,
  isAdmin,
  roleLabel,
  type Department,
  type ManagedUser,
  type Role,
} from "../../../types/access";

// Administración de roles y departamentos (solo Admin). Rol y departamento
// se asignan con DOS controles separados y cada uno manda solo su campo a
// PATCH /users/{id}/assignment: así se ve en la propia interfaz que son ejes
// independientes. La API vuelve a exigir Admin en cada llamada.

type AccessData = { roles: Role[]; departments: Department[]; users: ManagedUser[] };

function errorText(caught: unknown, fallback: string): string {
  if (caught instanceof ApiFieldError) return caught.fieldErrors.map((item) => item.message).join(" ");
  return caught instanceof Error && caught.message ? caught.message : fallback;
}

export default function AdminAccessPage() {
  const profile = useAccessProfile();

  if (!profile) {
    return (
      <main className="shell" style={{ padding: "24px 0 42px" }}>
        <section className="panel">
          <p style={{ margin: 0 }}>Verificando permisos…</p>
        </section>
      </main>
    );
  }

  if (!isAdmin(profile)) {
    return (
      <main className="shell" style={{ padding: "24px 0 42px" }}>
        <section className="panel" role="alert">
          <h1 style={{ marginTop: 0 }}>Acceso denegado (403)</h1>
          <p style={{ margin: 0, color: "var(--muted)" }}>
            La administración de roles y departamentos es exclusiva del rol Admin. Tu rol actual es{" "}
            {roleLabel(profile.role)}.
          </p>
        </section>
      </main>
    );
  }

  return <AdminAccessContent />;
}

function AdminAccessContent() {
  const fetchAll = useCallback(async (): Promise<AccessData> => {
    const [roles, departments, users] = await Promise.all([
      accessApi.listRoles(),
      accessApi.listDepartments(),
      accessApi.listUsers(),
    ]);
    return { roles, departments, users };
  }, []);
  const { data, isLoading, error, reload } = useAsyncData<AccessData>(
    fetchAll,
    "No se pudo cargar la administración de accesos. Inténtalo de nuevo.",
  );
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setNotice({ kind: "ok", text: done });
      reload();
    } catch (caught) {
      setNotice({ kind: "error", text: errorText(caught, "No se pudo guardar el cambio.") });
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px", display: "grid", gap: 16 }}>
      <section className="panel">
        <h1 style={{ marginTop: 0 }}>Roles y accesos</h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          El <strong>rol</strong> decide qué puede hacer cada persona; el <strong>departamento</strong>, qué datos le
          competen. Cambiar uno nunca cambia el otro.
        </p>
      </section>

      {notice && (
        <p role="status" className={notice.kind === "ok" ? "success-text" : "error-text"}>
          {notice.text}
        </p>
      )}

      <AsyncSection isLoading={isLoading} error={error} onRetry={reload} loadingLabel="Cargando accesos…">
        {data && (
          <>
            <UsersPanel data={data} busy={busy} run={run} />
            <RolesPanel roles={data.roles} busy={busy} run={run} />
            <DepartmentsPanel departments={data.departments} busy={busy} run={run} />
          </>
        )}
      </AsyncSection>
    </main>
  );
}

type Runner = (action: () => Promise<unknown>, done: string) => Promise<void>;

function UsersPanel({ data, busy, run }: { data: AccessData; busy: boolean; run: Runner }) {
  const [draft, setDraft] = useState({ email: "", password: "", name: "", role_id: "", department_id: "" });

  const create = (event: React.FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await accessApi.createUser({ ...draft, name: draft.name || undefined });
      setDraft({ email: "", password: "", name: "", role_id: "", department_id: "" });
    }, "Cuenta creada.");
  };

  return (
    <section className="panel" style={{ overflowX: "auto" }}>
      <h2 style={{ marginTop: 0 }}>Usuarios</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Nombre</th>
            <th>Rol</th>
            <th>Departamento</th>
          </tr>
        </thead>
        <tbody>
          {data.users.map((user) => (
            <tr key={user.id}>
              <td>{user.name ?? "Sin nombre"}</td>
              <td>
                <select
                  aria-label={`Rol de ${user.name ?? user.id}`}
                  value={user.role_id ?? ""}
                  disabled={busy}
                  onChange={(event) =>
                    void run(() => accessApi.assign(user.id, { role_id: event.target.value }), "Rol actualizado.")
                  }
                >
                  {!user.role_id && <option value="">Sin rol</option>}
                  {data.roles.map((role) => (
                    <option key={role.id} value={role.id}>
                      {roleLabel(role.code, role.name)}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <select
                  aria-label={`Departamento de ${user.name ?? user.id}`}
                  value={user.department_id ?? ""}
                  disabled={busy}
                  onChange={(event) =>
                    void run(
                      () => accessApi.assign(user.id, { department_id: event.target.value }),
                      "Departamento actualizado.",
                    )
                  }
                >
                  {!user.department_id && <option value="">Sin departamento</option>}
                  {data.departments.map((department) => (
                    <option key={department.id} value={department.id}>
                      {departmentLabel(department.code, department.name)}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Crear cuenta</h3>
      <form onSubmit={create} className="form-grid" style={{ alignItems: "end" }}>
        <label>
          Email
          <input type="email" required value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
        </label>
        <label>
          Contraseña inicial
          <input
            type="password"
            required
            minLength={8}
            value={draft.password}
            onChange={(e) => setDraft({ ...draft, password: e.target.value })}
          />
        </label>
        <label>
          Nombre
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </label>
        <label>
          Rol
          <select required value={draft.role_id} onChange={(e) => setDraft({ ...draft, role_id: e.target.value })}>
            <option value="">Elige un rol</option>
            {data.roles.map((role) => (
              <option key={role.id} value={role.id}>
                {roleLabel(role.code, role.name)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Departamento
          <select
            required
            value={draft.department_id}
            onChange={(e) => setDraft({ ...draft, department_id: e.target.value })}
          >
            <option value="">Elige un departamento</option>
            {data.departments.map((department) => (
              <option key={department.id} value={department.id}>
                {departmentLabel(department.code, department.name)}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Crear cuenta
        </button>
      </form>
    </section>
  );
}

function RolesPanel({ roles, busy, run }: { roles: Role[]; busy: boolean; run: Runner }) {
  const [draft, setDraft] = useState({ code: "", name: "", rank: 1 });
  return (
    <section className="panel" style={{ overflowX: "auto" }}>
      <h2 style={{ marginTop: 0 }}>Roles</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Rol</th>
            <th>Rango</th>
            <th>Capacidades</th>
            <th>Nombre visible</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role.id}>
              <td>
                <code>{role.code}</code>
              </td>
              <td>{role.rank}</td>
              <td style={{ fontSize: 13 }}>{role.capabilities.join(", ")}</td>
              <td>
                <RenameField
                  label={`Nombre del rol ${role.code}`}
                  initial={role.name}
                  disabled={busy}
                  onSave={(name) => void run(() => accessApi.renameRole(role.id, name), "Rol renombrado.")}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="form-grid"
        style={{ alignItems: "end", marginTop: 12 }}
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await accessApi.createRole(draft);
            setDraft({ code: "", name: "", rank: 1 });
          }, "Rol creado.");
        }}
      >
        <label>
          Código (snake_case)
          <input required pattern="[a-z][a-z0-9_]{1,39}" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} />
        </label>
        <label>
          Nombre
          <input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </label>
        <label>
          Rango (hereda capacidades)
          <select value={draft.rank} onChange={(e) => setDraft({ ...draft, rank: Number(e.target.value) })}>
            <option value={1}>1 · como Empleado</option>
            <option value={2}>2 · como Supervisor</option>
            <option value={3}>3 · como Admin</option>
          </select>
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Crear rol
        </button>
      </form>
    </section>
  );
}

function DepartmentsPanel({ departments, busy, run }: { departments: Department[]; busy: boolean; run: Runner }) {
  const [draft, setDraft] = useState({ code: "", name: "" });
  return (
    <section className="panel" style={{ overflowX: "auto" }}>
      <h2 style={{ marginTop: 0 }}>Departamentos</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Departamento</th>
            <th>Nombre (API)</th>
          </tr>
        </thead>
        <tbody>
          {departments.map((department) => (
            <tr key={department.id}>
              <td>{departmentLabel(department.code, department.name)}</td>
              <td>
                <RenameField
                  label={`Nombre del departamento ${department.code}`}
                  initial={department.name}
                  disabled={busy}
                  onSave={(name) =>
                    void run(() => accessApi.renameDepartment(department.id, name), "Departamento renombrado.")
                  }
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="form-grid"
        style={{ alignItems: "end", marginTop: 12 }}
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            await accessApi.createDepartment(draft);
            setDraft({ code: "", name: "" });
          }, "Departamento creado.");
        }}
      >
        <label>
          Código (snake_case)
          <input required pattern="[a-z][a-z0-9_]{1,39}" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} />
        </label>
        <label>
          Nombre
          <input required value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        </label>
        <button type="submit" className="primary" disabled={busy}>
          Crear departamento
        </button>
      </form>
    </section>
  );
}

function RenameField({
  label,
  initial,
  disabled,
  onSave,
}: {
  label: string;
  initial: string;
  disabled: boolean;
  onSave: (name: string) => void;
}) {
  const [value, setValue] = useState(initial);
  const changed = value.trim() !== initial && value.trim().length > 0;
  return (
    <div style={{ display: "flex", gap: 6 }}>
      <input aria-label={label} value={value} onChange={(event) => setValue(event.target.value)} />
      <button type="button" disabled={disabled || !changed} onClick={() => onSave(value.trim())}>
        Guardar
      </button>
    </div>
  );
}
