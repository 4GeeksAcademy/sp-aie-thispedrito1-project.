# Servidor simulado para demostrar el endurecimiento (feature/owasp-top10-audit).
#
# HealthCore no tiene servidor desplegado: este Ubuntu 24.04 reproduce la
# configuración típica de un VPS recién creado, que es la línea base que
# describe el ticket: root entra por SSH con contraseña y no hay firewall.
# infra/hardening/run-lab.sh lo arranca, mide, aplica harden.sh y vuelve a medir.
#
# Limitación honesta: es un contenedor, no una VM. Comparte el kernel con
# Docker Desktop y no tiene systemd (harden.sh recarga sshd por señal). ufw sí
# funciona de verdad dentro (iptables con --cap-add NET_ADMIN).
#
# SOLO LABORATORIO: la contraseña de root es una cadena sintética y la imagen
# se borra al terminar run-lab.sh. Nunca usar esta imagen para nada más.
FROM ubuntu:24.04

ARG LAB_ROOT_PASSWORD=lab-root-password-not-secret

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       openssh-server openssh-client ufw sudo iproute2 netcat-openbsd sshpass procps \
    && rm -rf /var/lib/apt/lists/*

# Línea base insegura (a propósito): root con contraseña por SSH.
RUN echo "root:${LAB_ROOT_PASSWORD}" | chpasswd \
    && printf 'PermitRootLogin yes\nPasswordAuthentication yes\n' >> /etc/ssh/sshd_config \
    && mkdir -p /run/sshd

EXPOSE 22
CMD ["/usr/sbin/sshd", "-D", "-e"]
