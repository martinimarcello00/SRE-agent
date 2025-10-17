import requests
import datetime
from dotenv import load_dotenv
import os
import logging
from kubernetes import client, config


class JaegerAPI():
    def __init__(self, jaeger_url: str = "http://localhost:16686") -> None:
        load_dotenv()
        self.jaeger_url = jaeger_url

        # Create kubernetes client
        config.load_kube_config()
        self.k8sClient = client.CoreV1Api()

        self.services = self.get_services_list()

    def get_services_list(self):
        """Get all the service names in the cluster"""
        service_list = self.k8sClient.list_service_for_all_namespaces()
        services_names = []
        for service in service_list.items:
            services_names.append(service.metadata.name)
        return services_names
    
    def get_jaeger_traces(self, service: str, limit: int = 20, lookback: str = "5m"):
        """Fetches traces from the Jaeger Query API."""
        logging.info(f"Querying Jaeger for '{service}' traces...")
        api_url = f"{self.jaeger_url}/api/traces"
        
        params = {
            "service": service, # name of the service to query traces for
            "limit": limit, # maximum number of traces to return
            "lookback": lookback, # time duration to look back (e.g., "1h", "30m")
        }

        try:
            response = requests.get(api_url, params=params)
            response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
            return response.json()["data"]
        except requests.exceptions.RequestException as e:
            logging.error(f"Error connecting to Jaeger: {e}")
            return None
        except KeyError:
            logging.error("Unexpected response format from Jaeger. 'data' key not found.")
            return None

    def process_trace(self, trace):
        """Extracts latency, service sequence, and error details from a single trace."""
        
        # Find the Root Span and Total Latency
        root_span = None
        for span in trace["spans"]:
            if not span.get("references"):
                root_span = span
                break
                
        if not root_span:
            return None

        latency_ms = root_span["duration"] / 1000.0

        # Check for Errors and Extract Messages
        has_error = False
        error_message = "N/A"
        error_details = [] # Store multiple error messages if they exist

        for span in trace["spans"]:
            is_error_span = False
            for tag in span.get("tags", []):
                if tag.get("key") == "error" and tag.get("value") is True:
                    has_error = True
                    is_error_span = True
                    break
            
            # If this span has the error, search its logs for the reason
            if is_error_span:
                for log in span.get("logs", []):
                    # Find fields like 'event: error', 'message', or 'stack'
                    log_fields = {field['key']: field['value'] for field in log.get("fields", [])}
                    if log_fields.get("event") == "error":
                        if "message" in log_fields:
                            error_details.append(log_fields["message"])
                        if "stack" in log_fields: # Stack traces can be verbose but useful
                            error_details.append(log_fields["stack"].split('\n')[0]) # Get first line of stack
        
        if error_details:
            error_message = "; ".join(error_details) # Join multiple messages

        # Determine the Sequence of Services
        service_map = {p_id: p_info["serviceName"] for p_id, p_info in trace["processes"].items()}
        sorted_spans = sorted(trace["spans"], key=lambda s: s["startTime"])
        
        service_sequence = []
        last_service = None
        for span in sorted_spans:
            service_name = service_map.get(span["processID"])
            if service_name and service_name != last_service:
                service_sequence.append(service_name)
                last_service = service_name
                
        result = {
            "traceID": trace["traceID"],
            "latency_ms": latency_ms,
            "has_error": has_error,
            "sequence": " -> ".join(service_sequence)
        }
        
        if has_error:
            result["error_message"] = error_message
        
        return result
    
    def get_processed_traces(self, service: str, limit: int = 20, lookback: str = "5m", only_errors: bool = False) -> dict:
        results = {}

        if service not in self.services:
            results["error"] = f"The service {service} does not exist"
            return results

        results["service"] = service
        results["traces"] = []
        
        traces = self.get_jaeger_traces(service, limit, lookback)
        
        if traces is None:
            logging.error(f"Failed to retrieve traces for service '{service}'. Check Jaeger connectivity and service name.")
            results["error"] = "Failed to fetch traces from Jaeger"
            return results
        
        if not traces:
            logging.warning(f"No traces found for service '{service}' with lookback '{lookback}'.")
            results["error"] = "No traces found"
            return results
        
        for trace in traces:
            trace_data = self.process_trace(trace)
            if trace_data:
                if only_errors and not trace_data["has_error"]:
                    continue
                results["traces"].append(trace_data)

        results["traces_count"] = len(results["traces"])

        return results
        
    def get_trace(self, trace_id: str):
        """Fetches a single trace by trace ID from Jaeger."""
        logging.info(f"Querying Jaeger for trace ID: {trace_id}")
        api_url = f"{self.jaeger_url}/api/traces/{trace_id}"
        
        try:
            response = requests.get(api_url)
            response.raise_for_status()
            trace_data = response.json()
            
            if "data" in trace_data and len(trace_data["data"]) > 0:
                return trace_data["data"][0]
            else:
                logging.warning(f"No trace found with ID: {trace_id}")
                return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Error connecting to Jaeger: {e}")
            return None
        except (KeyError, IndexError) as e:
            logging.error(f"Unexpected response format from Jaeger: {e}")
            return None
        
    # TODO: Implement function to filter metrics by minduration or affected by errors