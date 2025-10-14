from neo4j import GraphDatabase
from dotenv import load_dotenv
import os
import logging

class DataGraph():
    def __init__(self, uri:str, username:str, pw:str) -> None:
        self.driver = None
        auth = (username, pw)
        try:
            self.driver = GraphDatabase.driver(uri = uri, auth = auth)
        except Exception as e:
            logging.error("Error creating the driver: ", e)
        self.services = self.get_services()

    def close(self):
        """Close the connection to the kubernetes driver"""
        if self.driver is not None:
            self.driver.close()
        logging.info("neo4j driver closed")
        
    def query(self, query, parameters=None, db=None):
        """Query neo4j database"""
        assert self.driver is not None, "Driver not initialized!"
        session = None
        response = None
        try: 
            session = self.driver.session(database=db) if db is not None else self.driver.session() 
            result = session.run(query, parameters)
            response = [record.data() for record in result]  # Convert records to dictionaries
        except Exception as e:
            logging.error("Query failed:", e)
        finally: 
            if session is not None:
                session.close()
        return response
    
    def drop_datagraph(self):
        """Drop all nodes and relationships in the database after confirmation."""
        confirmation = input("Are you sure you want to drop all data in the database? Type 'yes' to confirm: ")
        if confirmation.lower() == 'yes':
            try:
                self.query("MATCH (n) DETACH DELETE n")
                logging.info("All data has been dropped from the database.")
            except Exception as e:
                logging.error("Failed to drop all data:", e)
        else:
            logging.info("Operation canceled.")

    def create_datagraph(self, file_path: str):
        """Create the datagraph by executing queries from a file."""
        if not os.path.exists(file_path):
            logging.error(f"File not found: {file_path}")
            return
        
        try:
            with open(file_path, 'r') as file:
                queries = file.read()
            
            # Split queries by semicolon and execute each query
            for query in queries.split(';'):
                query = query.strip()
                if query:  # Skip empty queries
                    self.query(query)
            logging.info("Datagraph created successfully.")
        except Exception as e:
            logging.error("Failed to create datagraph:", e)

    def get_services(self) -> list:
        """Return all the kubernetes services in the cluster"""
        result = self.query("MATCH (s:Service) RETURN s.name")
        services = [record["s.name"] for record in result] if result else []
        return services
    
    def get_services_used_by(self, service: str) -> str | list:
        """Return all the services that are used by the given service to complete its tasks"""
        if service not in self.services:
            return f"The service {service} doesn't exist in the cluster."
        query = "MATCH (s:Service {name: $service_name})-[:CALLS]->(c:Service) RETURN c.name"
        params = {"service_name": service}
        result = self.query(query, params)
        services_used = [record["c.name"] for record in result] if result else []
        return services_used
    
    def get_dependencies(self, service: str) -> str | dict:
        """Retrieves all dependencies for a specified service from kubernetes cluster."""
        if service not in self.services:
            return f"The service {service} doesn't exist in the cluster."
        query = """
        MATCH (s:Service {name: $service_name})-[:USES]->(dependency)
        RETURN dependency.name AS dependencyName, labels(dependency)[0] AS dependencyType
        """
        params = {"service_name": service}
        result = self.query(query, params)

        if len(result) > 0:
            return {record["dependencyName"]: record["dependencyType"] for record in result}
        else:
            return f"The service {service} has no dependencies"
    
    def get_service_summary(self, service: str) -> str:
        """
        Generates a summary of a given service, including the services it calls and its dependencies (for LLM purposes).
        """
        if service not in self.services:
            return f"The service {service} doesn't exist in the cluster."
        services_used = self.get_services_used_by(service)
        dependencies = self.get_dependencies(service)

        summary = f"The service {service} "
        if len(services_used) > 0:
            summary += f"uses {len(services_used)} services to complete its tasks: {', '.join(services_used)}."
        else:
            summary += f"doesn't use any other services to complete its tasks."

        if len(dependencies) > 0:
            summary += f" It has the following {len(dependencies)} dependencies: " + ", ".join([f"{dep['serviceName']} ({dep['serviceType']})" for dep in dependencies]) + "."

        return summary