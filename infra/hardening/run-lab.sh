#!/usr/bin/env bash
# Laboratorio de endurecimiento: antes → harden.sh → después, con evidencia.
#
# Desde la raíz del repo:   bash infra/hardening/run-lab.sh
#
# 1. Construye el servidor simulado (lab/Dockerfile) y un "atacante" con la
#    misma imagen, los dos en una red Docker propia.
# 2. Dentro del servidor deja escuchando 8000 (la API) y 5432 (Postgres), como
#    un despliegue descuidado.
# 3. ANTES: verify.sh dentro del servidor + pruebas reales desde el atacante.
# 4. Aplica harden.sh con una clave generada para esta corrida.
# 5. DESPUÉS: las mismas pruebas.
# 6. Escribe docs/security/evidence/hardening-{before,after}.txt y lo borra
#    todo (contenedores, red, imagen y la clave temporal).
#
# Requisitos: Docker funcionando. No toca nada fuera de sus contenedores.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="${ROOT}/infra/hardening"
EVIDENCE="${ROOT}/docs/security/evidence"
IMAGE="healthcore-hardening-lab:dev"
NET="hc-lab-net"
SERVER="hc-lab-server"
ATTACKER="hc-lab-attacker"
LAB_ROOT_PASSWORD="lab-root-password-not-secret"   # sintética, igual que en lab/Dockerfile
KEYDIR="$(mktemp -d)"

cleanup() {
  docker rm -f "${SERVER}" "${ATTACKER}" >/dev/null 2>&1 || true
  docker network rm "${NET}" >/dev/null 2>&1 || true
  docker rmi "${IMAGE}" >/dev/null 2>&1 || true
  rm -rf "${KEYDIR}"
}
trap cleanup EXIT

log() { printf '[lab] %s\n' "$*" >&2; }

SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -o BatchMode=no -o LogLevel=ERROR"

attempt() {  # attempt "descripción" <comando en el atacante>
  local label="$1"; shift
  if docker exec "${ATTACKER}" bash -c "$*" >/dev/null 2>&1; then
    printf '  %-58s ACEPTADO\n' "${label}"
  else
    printf '  %-58s rechazado\n' "${label}"
  fi
}

port() {
  if docker exec "${ATTACKER}" nc -z -w 3 "${SERVER}" "$1" >/dev/null 2>&1; then
    printf '  %-58s ABIERTO\n' "puerto $1 ($2) desde otra máquina"
  else
    printf '  %-58s cerrado\n' "puerto $1 ($2) desde otra máquina"
  fi
}

probe() {
  echo "== Pruebas desde otra máquina de la red (${ATTACKER} → ${SERVER})"
  attempt "SSH root con contraseña" \
    "sshpass -p '${LAB_ROOT_PASSWORD}' ssh ${SSH_OPTS} -o PubkeyAuthentication=no -o PreferredAuthentications=password root@${SERVER} true"
  attempt "SSH healthcore con contraseña" \
    "sshpass -p cualquiera ssh ${SSH_OPTS} -o PubkeyAuthentication=no -o PreferredAuthentications=password healthcore@${SERVER} true"
  attempt "SSH healthcore con su clave" \
    "ssh ${SSH_OPTS} -o BatchMode=yes -i /root/lab_key healthcore@${SERVER} true"
  attempt "SSH root con la clave del operador" \
    "ssh ${SSH_OPTS} -o BatchMode=yes -i /root/lab_key root@${SERVER} true"
  port 8000 "API FastAPI"
  port 5432 "PostgreSQL"
  port 22 "SSH"
}

snapshot() {  # snapshot before|after "título"
  local out="${EVIDENCE}/hardening-$1.txt"
  {
    echo "# Endurecimiento del servidor — $2"
    echo "# $(date -u +%Y-%m-%dT%H:%MZ) · servidor simulado Ubuntu 24.04 (infra/hardening/lab/Dockerfile)"
    echo "# Generado por: bash infra/hardening/run-lab.sh"
    echo
    echo "\$ verify.sh   (dentro del servidor, como root, solo lectura)"
    docker exec "${SERVER}" bash /opt/hardening/verify.sh || true
    echo
    probe
  } >"${out}"
  log "evidencia escrita en ${out#"${ROOT}/"}"
}

mkdir -p "${EVIDENCE}"
log "construyendo ${IMAGE}"
docker build -q -t "${IMAGE}" "${HERE}/lab" >/dev/null
docker network create "${NET}" >/dev/null

# NET_ADMIN: ufw necesita tocar iptables dentro del contenedor.
docker run -d --name "${SERVER}" --hostname "${SERVER}" --network "${NET}" \
  --cap-add NET_ADMIN --cap-add NET_RAW "${IMAGE}" >/dev/null
docker run -d --name "${ATTACKER}" --network "${NET}" "${IMAGE}" sleep infinity >/dev/null

# Despliegue "descuidado": la API y Postgres escuchando hacia fuera.
docker exec -d "${SERVER}" nc -lk 8000
docker exec -d "${SERVER}" nc -lk 5432
docker cp "${HERE}/." "${SERVER}:/opt/hardening"

ssh-keygen -q -t ed25519 -N "" -C "healthcore-lab" -f "${KEYDIR}/lab_key"
docker cp "${KEYDIR}/lab_key" "${ATTACKER}:/root/lab_key"
docker exec "${ATTACKER}" chmod 600 /root/lab_key
docker cp "${KEYDIR}/lab_key.pub" "${SERVER}:/root/healthcore.pub"
sleep 2

snapshot before "ANTES (configuración por defecto del VPS)"

log "aplicando harden.sh"
docker exec -e OPS_PUBKEY_FILE=/root/healthcore.pub "${SERVER}" bash /opt/hardening/harden.sh >&2
sleep 2

snapshot after "DESPUÉS de harden.sh"
log "listo; contenedores, red, imagen y clave temporal se borran al salir"
