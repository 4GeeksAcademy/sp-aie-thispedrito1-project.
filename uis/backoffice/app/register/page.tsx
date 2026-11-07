import Link from "next/link";

// Roles y Permisos (2026-11-07): ya no hay registro público. Las cuentas las
// crea un Admin desde /admin/access, eligiendo rol y departamento por
// separado (POST /users exige Admin). La ruta se conserva para que los
// enlaces antiguos expliquen el cambio en vez de dar un 404.
export default function RegisterPage() {
  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 640, margin: "0 auto" }}>
        <h1 style={{ marginTop: 0 }}>Las cuentas las crea un administrador</h1>
        <p style={{ color: "var(--muted)" }}>
          Para cumplir con HIPAA y UK GDPR, cada cuenta del backoffice se da de alta con un rol (Empleado,
          Supervisor o Admin) y un departamento. Pide a un Admin de Tecnología que te cree la tuya.
        </p>
        <p style={{ marginBottom: 0 }}>
          ¿Ya tienes cuenta?{" "}
          <Link href="/login" style={{ color: "var(--brand)", fontWeight: 700 }}>
            Inicia sesión
          </Link>
        </p>
      </section>
    </main>
  );
}
