#!/usr/bin/env python3
import os
import time
import threading
import logging
from io import StringIO
from typing import Optional, Tuple

import yaml
from dotenv import load_dotenv
from kubernetes import client, config, watch
from kubernetes.client import ApiException
from kubernetes.stream import stream

# =============== Logging ===============
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
log = logger  # alias

# =============== Config común ===============
def load_kube_config():
    load_dotenv()
    kube_config_content = os.getenv("KUBE_CONFIG")
    if kube_config_content:
        cfg = yaml.safe_load(StringIO(kube_config_content))
        config.load_kube_config_from_dict(cfg)
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

# -------- Interruptores de funcionalidades --------
ENABLE_ALLOCATABLE = os.getenv("ENABLE_ALLOCATABLE", "true").lower() == "true"
ENABLE_SMOKE = os.getenv("ENABLE_SMOKE", "true").lower() == "true"

# -------- Variables v1 (watch de errores de admisión) --------
LABEL_SELECTOR = os.getenv("LABEL_SELECTOR", "runtime-class=nvidia")
POD_STATUS_PHASE = os.getenv("POD_STATUS_PHASE", "Failed")
POD_STATUS_REASON = os.getenv("POD_STATUS_REASON", "UnexpectedAdmissionError")

# Device plugin a reiniciar
PLUGIN_NAMESPACE = os.getenv("PLUGIN_NAMESPACE", "kube-system")
PLUGIN_NAME_SUBSTRINGS = os.getenv(
    "PLUGIN_NAME_SUBSTRINGS", "nvidia-device-plugin,gpu-feature-discovery"
).split(",")

# Periodicidades
PERIOD_ALLOCATABLE_SEC = int(os.getenv("PERIOD_ALLOCATABLE_SEC", "60"))
PERIOD_SMOKE_SEC = int(os.getenv("PERIOD_SMOKE_SEC", "60"))

# Opciones de ámbito por nodo (lo que pediste)
ONLY_THIS_NODE = os.getenv("ONLY_THIS_NODE", "true").lower() == "true"
SMOKE_ONLY_THIS_NODE = os.getenv("SMOKE_ONLY_THIS_NODE", "true").lower() == "true"

# Reiniciar plugin si falla el smoke-test (además de reiniciar el pod)
RESTART_PLUGIN_ON_SMOKE_FAIL = os.getenv("RESTART_PLUGIN_ON_SMOKE_FAIL", "true").lower() == "true"

# Anti-flapping: mínimo entre reinicios
BACKOFF_RESTART_SEC = int(os.getenv("BACKOFF_RESTART_SEC", "45"))
_last_restart_by_node = {}
_last_restart_by_pod = {}

# =============== Helpers de entorno/nodo ===============
def resolve_my_node_name(core: client.CoreV1Api) -> Optional[str]:
    try:
        # el pod name se expone por defecto en HOSTNAME
        pod_name = os.getenv("HOSTNAME")
        # el namespace actual está montado en este archivo
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            namespace = f.read().strip()

        if pod_name and namespace:
            p = core.read_namespaced_pod(name=pod_name, namespace=namespace)
            return p.spec.node_name
    except Exception as e:
        log.warning(f"[detect] No se pudo determinar nodeName por API: {e}")
    return None

# =============== Helpers v2 ===============
def ready_nodes(core: client.CoreV1Api):
    nodes = core.list_node().items
    out = []
    for n in nodes:
        conds = {c.type: c.status for c in (n.status.conditions or [])}
        if conds.get("Ready") == "True":
            out.append(n)
    return out

def get_node_gpu_allocatable(core: client.CoreV1Api, node_name: str) -> int:
    n = core.read_node(node_name)
    alloc = (n.status.allocatable or {}).get("nvidia.com/gpu", "0")
    try:
        return int(alloc)
    except Exception:
        return 0

def restart_device_plugin(core: client.CoreV1Api, node_name: Optional[str] = None):
    """
    Borra los pods del device-plugin/gfd (DaemonSet) con backoff por nodo,
    usando substrings en el nombre.
    """
    now = time.time()
    key = node_name or "_cluster_"
    last = _last_restart_by_node.get(key, 0)
    if now - last < BACKOFF_RESTART_SEC:
        log.info(f"[device-plugin] Backoff activo para {key}, saltando reinicio")
        return
    _last_restart_by_node[key] = now

    try:
        all_pods = core.list_namespaced_pod(namespace=PLUGIN_NAMESPACE).items
        substrs = [s.strip().lower() for s in PLUGIN_NAME_SUBSTRINGS if s.strip()]

        pods = []
        for p in all_pods:
            if node_name and (not p.spec or p.spec.node_name != node_name):
                continue
            pname = (p.metadata.name or "").lower()
            if any(s in pname for s in substrs):
                pods.append(p)

        if not pods:
            target = f"en nodo {node_name}" if node_name else "en el clúster"
            log.warning(
                f"[device-plugin] No se encontraron pods a reiniciar {target} en ns '{PLUGIN_NAMESPACE}' "
                f"(fallback={PLUGIN_NAME_SUBSTRINGS})"
            )
            return

        for p in pods:
            try:
                core.delete_namespaced_pod(
                    name=p.metadata.name,
                    namespace=PLUGIN_NAMESPACE,
                    body=client.V1DeleteOptions(propagation_policy="Background"),
                )
                log.warning(f"[device-plugin] Reiniciado{' en ' + node_name if node_name else ''}: {p.metadata.name}")
            except ApiException as e:
                if e.status != 404:
                    log.error(f"[device-plugin] Error borrando {p.metadata.name}: {e}")

    except ApiException as e:
        log.error(f"[device-plugin] Fallo listando pods: {e}")

def run_nvidia_smi_in_pod(core: client.CoreV1Api, pod_name: str, namespace: str) -> Tuple[bool, str]:
    """Ejecuta nvidia-smi en un pod en ejecución. Devuelve (ok, salida o error)."""
    try:
        resp = stream(
            core.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["nvidia-smi"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        txt = (resp or "").strip()
        if "Driver Version" in txt and "Failed" not in txt:
            return True, txt
        return False, txt or "nvidia-smi no devolvió salida esperada"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

# =============== Funcionalidad v1: Watch de pods con error de admisión ===============
def watch_nvidia_pods():
    """
    Mantiene el comportamiento de la v1:
    - Hace watch de pods con LABEL_SELECTOR.
    - Si un pod entra en fase POD_STATUS_PHASE con reason POD_STATUS_REASON -> se borra.
    - Gestiona resourceVersion y errores 410 (reset del watch).
    """
    load_kube_config()
    v1 = client.CoreV1Api()
    w = watch.Watch()

    label_selector = LABEL_SELECTOR
    pod_status_phase = POD_STATUS_PHASE
    pod_status_reason = POD_STATUS_REASON
    resource_version = None

    while True:
        try:
            for event in w.stream(
                v1.list_pod_for_all_namespaces,
                label_selector=label_selector,
                resource_version=resource_version,
                timeout_seconds=60
            ):
                pod = event['object']
                resource_version = pod.metadata.resource_version
                if pod.status.phase == pod_status_phase and pod.status.reason == pod_status_reason:
                    logger.info(f"Error detected – Pod: {pod.metadata.name}, Namespace: {pod.metadata.namespace}")
                    try:
                        v1.delete_namespaced_pod(
                            name=pod.metadata.name,
                            namespace=pod.metadata.namespace,
                            body=client.V1DeleteOptions(propagation_policy="Background")
                        )
                        logger.info(f"Deleted pod {pod.metadata.name} in {pod.metadata.namespace}")
                    except ApiException as delete_err:
                        if delete_err.status == 404:
                            logger.info(f"Pod {pod.metadata.name} not found, skipping")
                        else:
                            logger.error(f"Deletion failed: {delete_err}")
        except ApiException as e:
            if e.status == 410:
                logger.warning("Stale watch detected (410 Gone), resetting resource_version")
                resource_version = None
                continue
            logger.error(f"Watch stream failed: {e}")
        except Exception as exc:
            logger.error(f"Unexpected error in watch loop: {exc}")

# =============== Timers ajustados al propio nodo ===============
def timer_allocatable():
    """
    Timer 1: revisa el nodo local (o todos si ONLY_THIS_NODE=false);
             si allocatable GPU = 0 en el ámbito elegido, reinicia device-plugin en ese nodo.
    """
    load_kube_config()
    core = client.CoreV1Api()
    my_node = resolve_my_node_name(core)

    while True:
        try:
            node_list = []
            if ONLY_THIS_NODE:
                if not my_node:
                    log.warning("[alloc] No se pudo determinar MY_NODE_NAME; haciendo fallback a todos los nodos Ready")
                    node_list = ready_nodes(core)  # <--- fallback a todos
                else:
                    # Validamos que el nodo esté Ready
                    try:
                        n = core.read_node(my_node)
                        conds = {c.type: c.status for c in (n.status.conditions or [])}
                        if conds.get("Ready") == "True":
                            node_list = [n]
                        else:
                            log.warning(f"[alloc] Nodo {my_node} no está Ready")
                    except Exception as e:
                        log.error(f"[alloc] No se pudo leer el nodo {my_node}: {e}")
            else:
                node_list = ready_nodes(core)

            if not node_list:
                log.warning("[alloc] No hay nodos a revisar en este ciclo")

            for n in node_list:
                name = n.metadata.name
                alloc = get_node_gpu_allocatable(core, name)
                log.info(f"[alloc] {name}: nvidia.com/gpu allocatable = {alloc}")
                if alloc == 0:
                    log.warning(f"[alloc] {name} sin GPUs allocatable → reiniciando device-plugin")
                    restart_device_plugin(core, node_name=name)

        except Exception as e:
            log.error(f"[alloc] Error: {e}")

        time.sleep(PERIOD_ALLOCATABLE_SEC)

def timer_smoke():
    """
    Timer 2: ejecuta nvidia-smi en pods seleccionados (por label) y opcionalmente solo del propio nodo.
             Si falla, reinicia ese pod; y opcionalmente también el device-plugin del nodo.
    """
    load_kube_config()
    core = client.CoreV1Api()
    my_node = resolve_my_node_name(core)

    while True:
        try:
            pods = core.list_pod_for_all_namespaces(
                label_selector=LABEL_SELECTOR
            ).items

            # Solo pods Running
            pods = [p for p in pods if p.status.phase == "Running"]

            # Opcional: limitar al propio nodo
            if SMOKE_ONLY_THIS_NODE:
                if my_node:
                    pods = [p for p in pods if getattr(p.spec, "node_name", None) == my_node]
                else:
                    log.warning("[smoke] No se pudo determinar MY_NODE_NAME; ejecutando sin filtro por nodo")

            if not pods:
                scope_msg = f" en nodo {my_node}" if (SMOKE_ONLY_THIS_NODE and my_node) else ""
                log.warning(f"[smoke] No hay pods con selector '{LABEL_SELECTOR}'{scope_msg}")

            for p in pods:
                name = p.metadata.name
                ns   = p.metadata.namespace
                node = p.spec.node_name

                ok, out = run_nvidia_smi_in_pod(core, name, ns)
                if ok:
                    log.info(f"[smoke] {ns}/{name} ({node}) OK")
                    continue

                # Falla -> reiniciar ese pod
                log.warning(f"[smoke] {ns}/{name} ({node}) FAIL → {out}")

                # Backoff por pod
                key = f"{ns}/{name}"
                now = time.time()
                last = _last_restart_by_pod.get(key, 0)
                if now - last < BACKOFF_RESTART_SEC:
                    log.info(f"[smoke] Backoff activo para pod {key}, saltando reinicio")
                    continue
                _last_restart_by_pod[key] = now

                try:
                    core.delete_namespaced_pod(
                        name=name,
                        namespace=ns,
                        body=client.V1DeleteOptions(propagation_policy="Background"),
                    )
                    log.warning(f"[smoke] Reiniciado pod {ns}/{name} por fallo de nvidia-smi")
                except ApiException as e:
                    if e.status != 404:
                        log.error(f"[smoke] Error reiniciando pod {ns}/{name}: {e}")

                # Opcionalmente, también reiniciar el device-plugin del nodo
                if RESTART_PLUGIN_ON_SMOKE_FAIL and node:
                    log.warning(f"[smoke] Reiniciando device-plugin en nodo {node} por fallo de smoke-test")
                    restart_device_plugin(core, node_name=node)

        except Exception as e:
            log.error(f"[smoke] Error: {e}")

        time.sleep(PERIOD_SMOKE_SEC)

# =============== Main ===============
def main():
    threads = []

    # Watch v1 siempre activo (si quieres también switch aquí, dilo y lo añado)
    t_watch = threading.Thread(target=watch_nvidia_pods, daemon=True)
    threads.append(t_watch)

    if ENABLE_ALLOCATABLE:
        t_alloc = threading.Thread(target=timer_allocatable, daemon=True)
        threads.append(t_alloc)
    else:
        log.info("[main] ENABLE_ALLOCATABLE=false → desactivado timer_allocatable")

    if ENABLE_SMOKE:
        t_smoke = threading.Thread(target=timer_smoke, daemon=True)
        threads.append(t_smoke)
    else:
        log.info("[main] ENABLE_SMOKE=false → desactivado timer_smoke")

    for t in threads:
        t.start()

    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
