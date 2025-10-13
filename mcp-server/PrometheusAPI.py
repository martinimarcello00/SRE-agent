from prometheus_api_client import PrometheusConnect
from kubernetes import client, config

class PrometheusAPI:

    normal_metrics = [
        # cpu
        "container_cpu_usage_seconds_total",
        "container_cpu_user_seconds_total",
        "container_cpu_system_seconds_total",
        "container_cpu_cfs_throttled_seconds_total",
        "container_cpu_cfs_throttled_periods_total",
        "container_cpu_cfs_periods_total",
        "container_cpu_load_average_10s",
        # memory
        "container_memory_cache",
        "container_memory_usage_bytes",
        "container_memory_working_set_bytes",
        "container_memory_rss",
        "container_memory_mapped_file",
        # spec
        "container_spec_cpu_period",
        "container_spec_cpu_quota",
        "container_spec_memory_limit_bytes",
        "container_spec_cpu_shares",
        # threads
        "container_threads",
        "container_threads_max"
        # network
        "container_network_receive_errors_total",
        "container_network_receive_packets_dropped_total",
        "container_network_receive_packets_total",
        "container_network_receive_bytes_total",
        "container_network_transmit_bytes_total",
        "container_network_transmit_errors_total",
        "container_network_transmit_packets_dropped_total",
        "container_network_transmit_packets_total",
    ]

    network_metrics = [
        # network
        "container_network_receive_errors_total",
        "container_network_receive_packets_dropped_total",
        "container_network_receive_packets_total",
        "container_network_receive_bytes_total",
        "container_network_transmit_bytes_total",
        "container_network_transmit_errors_total",
        "container_network_transmit_packets_dropped_total",
        "container_network_transmit_packets_total",
    ]   

    def __init__(self, url:str, namespace:str) -> None:
        self.url = url
        # Create prometheus client
        try:
            self.prometheusClient = PrometheusConnect(self.url, disable_ssl=True)
        except Exception as e:
            print("Error connecting to prometheus server: ", e)
        
        # Create kubernetes client
        config.load_kube_config()
        self.k8sClient = client.CoreV1Api()

        self.namespace = namespace

        # Get list of pods and services in the namespace
        self.pods = self.get_pods_list()
        self.services = self.get_services_list()

    def get_pods_list(self):
        """Get all the pod names in the namespace"""
        pod_list = self.k8sClient.list_namespaced_pod(self.namespace)
        pod_names = []
        for pod in pod_list.items:
            pod_names.append(pod.metadata.name)
        return pod_names

    def get_services_list(self):
        """Get all the service names in the namespace"""
        service_list = self.k8sClient.list_namespaced_service(self.namespace)
        services_names = []
        for service in service_list.items:
            services_names.append(service.metadata.name)
        return services_names
    
    def get_pods_from_service(self, service: str):
        """Return all the pods connected to a service"""
        results = {}
        results["service_name"] = service
        results["namespace"] = self.namespace
        # Check if the service exist
        if service not in self.services:
            results["error"] = f"The service {service} does not exist in the {self.namespace} namespace."
            return results
        # Get the service
        requested_svc = self.k8sClient.read_namespaced_service(service, self.namespace)
        # Get the service's selectors
        selector = requested_svc.spec.selector
        # Prepare the label selectors to query all the pods connected to that service
        label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])
        # Get the pods with the corresponding label selector
        pods = self.k8sClient.list_namespaced_pod(self.namespace, label_selector=label_selector)
        results["pods"] = []
        for pod in pods.items:
            results["pods"].append({
                "pod_name" : pod.metadata.name,
                "pod_status" : pod.status.phase
            })
        return results
    
    def get_pod_metrics(self, pod_name:str):
        """
            Get all metrics (no Istio) for given pods - INSTANT VALUES ONLY.
            
            Args:
                pod_names (list): List of pod names
                namespace (str): Kubernetes namespace  
            
            Returns:
                dict: {pod_name: {metric_name: current_value}}
        """
        
        all_metrics = self.normal_metrics + self.network_metrics
        # Remove duplicates
        all_metrics = list(set(all_metrics))
        
        results = {}
        
        results["resource_type"] = "pod"
        results["resource_namespace"] = self.namespace
        results["resource_name"] = pod_name

        # Check if the pod exists
        if pod_name not in self.pods:
            results["error"] = f"The pod {pod_name} does not exist in the {self.namespace} namespace."
            return results
        
        results["metrics"] = {}

        for metric in all_metrics:
            try:
                # Instant query
                query = f'{metric}{{namespace="{self.namespace}", pod=~".*{pod_name}.*"}}'
                data = self.prometheusClient.custom_query(query=query)
                
                if data:
                    # Extract just the value from first result
                    if len(data) > 0 and 'value' in data[0]:
                        results["metrics"][metric] = float(data[0]['value'][1])
                    else:
                        results["metrics"][metric] = None
                else:
                    results["metrics"][metric] = None
                    
            except Exception as e:
                results["metrics"][metric] = f"Error: {str(e)}"

        return results