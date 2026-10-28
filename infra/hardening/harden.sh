#!/usr/bin/env bash
# Endurecimiento básico de un servidor Ubuntu para HealthCore
# (auditoría OWASP Top 10, feature/owasp-top10-audit).
#
# Hace cuatro cosas, en este orden, y se puede ejecutar varias veces:
#   1. Usuario operativo sin root (`healthcore`), solo con clave SSH y con un
#      sudo limitado a reiniciar/ver los servicios de la app.
#   2. SSH: sin root, sin contraseñas, solo `healthcore` (files/sshd-hardening.conf).
#   3. Carpetas con permisos explícitos: código, logs y configuración sensible.
#   4. Firewall (ufw): todo cerrado salvo ALLOWED_PORTS.
#
# Uso (como root, en el servidor):
#   OPS_PUBKEY_FILE=/root/healthcore.pub ./harden.sh
#
# Variables:
#   OPS_PUBKEY_FILE  clave pública del operador (obligatoria: sin ella el
#                    usuario quedaría sin forma de entrar y el script se para)
#   OPS_USER         usuario operativo (por defecto healthcore)
#   APP_ROOT         raíz de la app (por defecto /srv/healthcore)
#   ALLOWED_PORTS    puertos abiertos (por defecto "22/tcp 443/tcp": SSH y el
#                    proxy HTTPS; la API, Postgres, Redis y Qdrant NUNCA se abren)
#
# Ojo con Docker en un servidor real: los puertos que publica Docker se saltan
# ufw (Docker escribe sus propias reglas de iptables). Por eso docker-compose.yml
# publica todo en 127.0.0.1 y solo el proxy HTTPS escucha hacia fuera.

set -euo pipefail

OPS_USER="${OPS_USER:-healthcore}"
APP_ROOT="${APP_ROOT:-/srv/healthcore}"
ALLOWED_PORTS="${ALLOWED_PORTS:-22/tcp 443/tcp}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSHD_DROPIN="/etc/ssh/sshd_config.d/00-healthcore-hardening.conf"

log() { printf '[harden] %s\n' "$*"; }
die() { printf '[harden] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || die "ejecútalo como root (es la última vez que hace falta)"
[[ -n "${OPS_PUBKEY_FILE:-}" && -s "${OPS_PUBKEY_FILE}" ]] \
  || die "OPS_PUBKEY_FILE debe apuntar a la clave pública del operador"
grep -qE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-)' "${OPS_PUBKEY_FILE}" \
  || die "${OPS_PUBKEY_FILE} no parece una clave pública SSH"

for bin in sshd ufw visudo; do
  command -v "$bin" >/dev/null || die "falta '$bin' (apt-get install openssh-server ufw sudo)"
done

# --- 1. Usuario operativo -------------------------------------------------------
if id "${OPS_USER}" >/dev/null 2>&1; then
  log "usuario ${OPS_USER} ya existe"
else
  useradd --create-home --shell /bin/bash "${OPS_USER}"
  log "usuario ${OPS_USER} creado"
fi
# Sin contraseña utilizable: solo se entra con la clave.
passwd --lock "${OPS_USER}" >/dev/null

install -d -m 700 -o "${OPS_USER}" -g "${OPS_USER}" "/home/${OPS_USER}/.ssh"
install -m 600 -o "${OPS_USER}" -g "${OPS_USER}" "${OPS_PUBKEY_FILE}" "/home/${OPS_USER}/.ssh/authorized_keys"
log "clave pública instalada en /home/${OPS_USER}/.ssh/authorized_keys"

# Mínimo privilegio: el día a día es reiniciar la app y leer sus logs, no ser root.
SUDOERS_TMP="$(mktemp)"
cat >"${SUDOERS_TMP}" <<EOF
# Generado por infra/hardening/harden.sh: solo los servicios de HealthCore.
${OPS_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart healthcore-*, /usr/bin/systemctl status healthcore-*, /usr/bin/journalctl -u healthcore-*
EOF
visudo -cf "${SUDOERS_TMP}" >/dev/null || die "regla de sudoers inválida"
install -m 440 -o root -g root "${SUDOERS_TMP}" "/etc/sudoers.d/healthcore-ops"
rm -f "${SUDOERS_TMP}"
log "sudo limitado a los servicios healthcore-*"

# --- 2. SSH ---------------------------------------------------------------------
install -d -m 755 /etc/ssh/sshd_config.d
grep -qE '^\s*Include\s+/etc/ssh/sshd_config.d/\*\.conf' /etc/ssh/sshd_config \
  || die "sshd_config no incluye sshd_config.d/*.conf; añade el Include al principio"
sed "s/^AllowUsers .*/AllowUsers ${OPS_USER}/" "${SCRIPT_DIR}/files/sshd-hardening.conf" >"${SSHD_DROPIN}"
chmod 644 "${SSHD_DROPIN}"
install -d -m 755 /run/sshd
# Validar ANTES de recargar: un error de sintaxis aquí te deja fuera del servidor.
sshd -t || { rm -f "${SSHD_DROPIN}"; die "configuración de sshd inválida, revertida"; }

if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
elif [[ -s /run/sshd.pid ]]; then
  kill -HUP "$(cat /run/sshd.pid)"   # contenedor/laboratorio sin systemd
else
  pkill -HUP -o -x sshd
fi
log "sshd recargado: sin root, sin contraseñas, solo ${OPS_USER}"

# --- 3. Carpetas ----------------------------------------------------------------
#   app/     código desplegado: lo escribe el operador, nadie más lo lee.
#   logs/    la app escribe; el grupo adm (auditoría) puede leer. setgid para
#            que los archivos nuevos hereden el grupo.
#   config/  secretos (.env con DATABASE_URL, JWT_SECRET_KEY, LLM_API_KEY):
#            de root, la app solo puede LEERLOS. El operador no los cambia.
install -d -m 755 -o root -g root "${APP_ROOT}"
install -d -m 750 -o "${OPS_USER}" -g "${OPS_USER}" "${APP_ROOT}/app"
install -d -m 2750 -o "${OPS_USER}" -g adm "${APP_ROOT}/logs"
install -d -m 750 -o root -g "${OPS_USER}" "${APP_ROOT}/config"
if [[ -e "${APP_ROOT}/config/.env" ]]; then
  chown root:"${OPS_USER}" "${APP_ROOT}/config/.env"
  chmod 640 "${APP_ROOT}/config/.env"
fi
log "permisos aplicados en ${APP_ROOT}/{app,logs,config}"

# --- 4. Firewall ----------------------------------------------------------------
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for port in ${ALLOWED_PORTS}; do
  if [[ "${port}" == 22/tcp ]]; then
    ufw limit 22/tcp >/dev/null   # permite SSH pero frena ráfagas de intentos
  else
    ufw allow "${port}" >/dev/null
  fi
done
ufw --force enable >/dev/null
log "firewall activo: entrada denegada salvo ${ALLOWED_PORTS}"

log "listo. Comprueba con: ALLOWED_PORTS=\"${ALLOWED_PORTS}\" ${SCRIPT_DIR}/verify.sh"
