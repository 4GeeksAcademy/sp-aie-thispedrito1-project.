# infra/ — endurecimiento del servidor

Parte de la auditoría OWASP (`docs/security/owasp-top10-audit.md`; pendientes con plan en su §6).

```bash
bash infra/hardening/run-lab.sh     # Ubuntu 24.04 simulado + atacante: antes → harden.sh → después (lo borra todo al salir)
# En un Ubuntu real, como root:
OPS_PUBKEY_FILE=… infra/hardening/harden.sh && infra/hardening/verify.sh
```

- `harden.sh`: usuario `healthcore` con sudo limitado a `healthcore-*`, drop-in `00-` de sshd validado con `sshd -t`, `/srv/healthcore/{app,logs,config}`, ufw 22 con `limit` + 443. `verify.sh` solo lee.
- En un servidor, los puertos publicados por Docker se saltan ufw: por eso el compose publica todo en `127.0.0.1` y los Dockerfiles Python corren como `USER healthcore` (`ARG APP_UID=1000`). `tests/security/test_deploy_config.py` lo vigila para todo puerto nuevo.
- No verificado: build de los Dockerfiles con `USER healthcore`.
