from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException
from io import StringIO
import yaml
import logging
import os
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_kube_config():
    load_dotenv()
    kube_config_content = os.getenv('KUBE_CONFIG')
    if kube_config_content:
        cfg = yaml.safe_load(StringIO(kube_config_content))
        config.load_kube_config_from_dict(cfg)
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

def monitor_nvidia_pods():
    load_kube_config()
    v1 = client.CoreV1Api()
    w = watch.Watch()

    label_selector = os.getenv('LABEL_SELECTOR', 'runtime-class=nvidia')
    pod_status_phase = os.getenv('POD_STATUS_PHASE', 'Failed')
    pod_status_reason = os.getenv('POD_STATUS_REASON', 'UnexpectedAdmissionError')
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
                logger.warning("Stale watch detected (410 Gone), resetting resource_version")  # reset and retry
                resource_version = None
                continue
            logger.error(f"Watch stream failed: {e}")
        except Exception as exc:
            logger.error(f"Unexpected error in watch loop: {exc}")

if __name__ == "__main__":
    monitor_nvidia_pods()
