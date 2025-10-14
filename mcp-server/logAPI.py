from kubernetes import client, config

class LogAPI:
    def __init__(self, namespace:str) -> None:
        
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
    
    def get_pod_logs(self, pod_name: str, tail: int = 100, important: bool = True) -> str:
        # Check if the pod exists
        if pod_name not in self.pods:
            return f"The pod {pod_name} does not exist in the {self.namespace} namespace."
        
        logs = self.k8sClient.read_namespaced_pod_log(
            name=pod_name,
            namespace=self.namespace,
            tail_lines=tail,
        )

        if important:
            # Split logs into lines
            log_lines = logs.split('\n')

            # Only return lines containing 'ERROR', 'WARN', or 'CRITICAL'
            important_keywords = ["ERROR", "WARN", "CRITICAL"]

            # Return only the log lines that contains the important keywords
            filtered_logs = [line for line in log_lines if any(keyword in line for keyword in important_keywords)]

            results = ""

            if len(filtered_logs) > 0:
                # Count occurrences of each keyword
                error_count = sum(1 for line in filtered_logs if "ERROR" in line)
                warn_count = sum(1 for line in filtered_logs if "WARN" in line)
                critical_count = sum(1 for line in filtered_logs if "CRITICAL" in line)

                results = f"Found {len(filtered_logs)} important log entries:\n"
                results += f"ERROR: {error_count} lines\n"
                results += f"WARN: {warn_count} lines\n"
                results += f"CRITICAL: {critical_count} lines\n\n"
                results += "\n".join(filtered_logs)
            else:
                results += "No important log entries found, full log entries are appended\n"
                results += "\n".join(log_lines)
            
            return results
        else:
            return logs

