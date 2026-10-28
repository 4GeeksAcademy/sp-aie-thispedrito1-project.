#!/usr/bin/env bash
# Comprueba el endurecimiento del servidor SIN cambiar nada (solo lee).
# Una línea PASS/FAIL por requisito del ticket; el código de salida es el
# número de FAIL (0 = todo bien). Se ejecuta como root en el servidor:
#   ALLOWED_PORTS="22/tcp 443/tcp" ./verify.sh
#
# Es la evidencia "antes/después" de docs/security/owasp-top10-audit.md.

set -uo pipefail

OPS_USER="${OPS_USER:-healthcore}"
APP_ROOT="${APP_ROOT:-/srv/healthcore}"
ALLOWED_PORTS="${ALLOWED_PORTS:-22/tcp 443/tcp}"
FAILS=0

check() {  # check "descripción" <comando...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf 'PASS  %s\n' "${label}"
  else
    printf 'FAIL  %s\n' "${label}"
    FAILS=$((FAILS + 1))
  fi
}

sshd_option() { sshd -T 2>/dev/null | awk -v k="$1" '$1 == k { $1 = ""; sub(/^ /, ""); print; exit }'; }
is() { [[ "$1" == "$2" ]]; }
perms_are() { [[ "$(stat -c '%U:%G %a' "$1" 2>/dev/null)" == "$2" ]]; }

echo "== SSH (sshd -T: configuración efectiva)"
check "PermitRootLogin no"                 is "$(sshd_option permitrootlogin)" "no"
check "PasswordAuthentication no"          is "$(sshd_option passwordauthentication)" "no"
check "KbdInteractiveAuthentication no"    is "$(sshd_option kbdinteractiveauthentication)" "no"
check "AllowUsers ${OPS_USER}"             is "$(sshd_option allowusers)" "${OPS_USER}"
check "MaxAuthTries <= 3"                  test "$(sshd_option maxauthtries)" -le 3
check "AllowTcpForwarding no"              is "$(sshd_option allowtcpforwarding)" "no"

echo "== Usuario operativo"
check "${OPS_USER} existe"                 id "${OPS_USER}"
check "${OPS_USER} no es UID 0"            test "$(id -u "${OPS_USER}" 2>/dev/null || echo 0)" -ne 0
check "${OPS_USER} sin contraseña usable"  is "$(passwd -S "${OPS_USER}" 2>/dev/null | awk '{print $2}')" "L"
check "${OPS_USER} con clave autorizada"   test -s "/home/${OPS_USER}/.ssh/authorized_keys"
check "~/.ssh 700"                         perms_are "/home/${OPS_USER}/.ssh" "${OPS_USER}:${OPS_USER} 700"
check "authorized_keys 600"                perms_are "/home/${OPS_USER}/.ssh/authorized_keys" "${OPS_USER}:${OPS_USER} 600"
check "${OPS_USER} sin sudo completo"      bash -c "! sudo -l -U ${OPS_USER} 2>/dev/null | grep -qE '\(ALL( : ALL)?\) (NOPASSWD: )?ALL$'"
check "root es la única cuenta UID 0"      is "$(awk -F: '$3 == 0 {print $1}' /etc/passwd | tr '\n' ' ')" "root "

echo "== Carpetas"
check "${APP_ROOT}/app    ${OPS_USER}:${OPS_USER} 750" perms_are "${APP_ROOT}/app" "${OPS_USER}:${OPS_USER} 750"
check "${APP_ROOT}/logs   ${OPS_USER}:adm 2750"        perms_are "${APP_ROOT}/logs" "${OPS_USER}:adm 2750"
check "${APP_ROOT}/config root:${OPS_USER} 750"        perms_are "${APP_ROOT}/config" "root:${OPS_USER} 750"

echo "== Firewall (ufw)"
UFW="$(ufw status verbose 2>/dev/null)"
check "ufw activo"                          grep -q '^Status: active' <<<"${UFW}"
check "entrada denegada por defecto"        grep -q 'Default: deny (incoming)' <<<"${UFW}"
OPEN="$(ufw status 2>/dev/null | awk '/ALLOW|LIMIT/ && $1 !~ /\(v6\)/ {print $1}' | sort -u | tr '\n' ' ')"
WANT="$(tr ' ' '\n' <<<"${ALLOWED_PORTS}" | sort -u | tr '\n' ' ')"
check "solo abiertos: ${ALLOWED_PORTS}"     is "${OPEN}" "${WANT}"

echo "== Puertos escuchando (informativo)"
ss -tlnH 2>/dev/null | awk '{print "      " $4}' | sort -u

echo "== Resultado: ${FAILS} FAIL"
exit "${FAILS}"
