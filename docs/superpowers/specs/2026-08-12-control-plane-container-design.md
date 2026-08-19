# Contenedor del control-plane — diseño

Fecha: 2026-08-12
Repo: `e-ovrt_control-plane`
Motivo: cerrar B2 del relevamiento 114 sin cambiar la semántica del servicio.

> **Estado: descartado el 2026-08-13.** La decisión operacional vigente mantiene
> media-plane, control-plane y distribución como procesos del host. No se implementa
> ni mantiene una imagen propia del control-plane. El contenido siguiente queda como
> registro histórico del diseño evaluado, no como plan activo.
>
> ✎ 2026-08-19: revertido — el despliegue de plataforma completa en Docker Compose
> (pedido del usuario, ver `e-ovrt_experimental-setup/infra/platform/`) reintroduce la
> imagen. Implementada en `infra/docker/Dockerfile` de este repo, siguiendo el §2 de
> este diseño.

## 1. Alcance

Agregar una imagen reproducible del FastAPI control-plane. No se cambia su API, runtime de patrones,
configuración, transporte ZeroMQ ni layout de artefactos. La imagen es independiente del compose de
plataforma: la topología vigente sigue ejecutando control y distribución en el host.

## 2. Imagen

El `Dockerfile` usa `python:3.11-slim`, instala el paquete sin el extra pesado `labs`, ejecuta como
usuario no root y arranca:

```text
python -m uvicorn --factory eovrt_control.service.app:create_app --host 0.0.0.0 --port 8081
```

Declara el puerto 8081, un healthcheck contra `/healthz` y
`EOVRT_CONTROL_RUNS_DIR=/data/runs`. Configs, patterns, detections y runs se aportan mediante mounts;
la imagen no contiene datasets, pesos ni resultados del workspace.

## 3. Contrato de paths y red

Las configs enviadas por payload ya exigen rutas absolutas. Dentro de un contenedor, el operador debe
usar paths del contenedor y montar los mismos archivos allí. Los endpoints ZeroMQ también deben ser
alcanzables desde la red elegida; la imagen no traduce `localhost` ni reescribe YAML.

Esta explicitud evita una imagen que funcione solo por conocer la ubicación local de los repos
hermanos.

## 4. Verificación

- Build de la imagen con el contexto del repo.
- Arranque con un volumen temporal para `/data/runs`.
- `/healthz` y `/readyz` responden correctamente.
- Suite y Ruff existentes siguen limpios bajo Python 3.11.
- Si Docker no está disponible, se valida el Dockerfile estáticamente y se registra que el smoke de
  daemon quedó condicionado por el entorno, sin afirmar que fue ejecutado.
