# Imagen del control-plane (servicio HTTP :8081 + bus de alertas :5558).
# Diseño: docs/superpowers/specs/2026-08-12-control-plane-container-design.md §2
# (descartado el 2026-08-13, revertido el 2026-08-19 para el compose de plataforma).
#
# Build desde la RAIZ del repo (el contexto es el repo, no este directorio):
#   docker build -f infra/docker/Dockerfile -t eovrt-control .
#
# Solo dependencias del nucleo — NUNCA el extra [labs] (torch/opencv/transformers).
# Las configs de corrida llegan por payload (POST /api/runs) o por volumenes montados;
# configs/ se copia porque contiene los pattern sets que el servicio resuelve por path.

FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY configs/ ./configs/

RUN pip install --no-cache-dir .

# Artefactos de corrida fuera de la imagen (ADR: el JSONL es la verdad).
ENV EOVRT_CONTROL_RUNS_DIR=/data/runs
VOLUME /data/runs

# 8081: servicio HTTP (eovrt-control serve). 5558: bus de alertas PUB
# (alert_bus.enabled, bind tcp://0.0.0.0:5558 cuando se habilita).
EXPOSE 8081
EXPOSE 5558

# python:slim no trae curl: healthcheck con stdlib.
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8081/healthz', timeout=3).status==200 else 1)"]

CMD ["eovrt-control", "serve", "--host", "0.0.0.0", "--port", "8081"]
