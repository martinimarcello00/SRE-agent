from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

class DataGraph():
    def __init__(self, uri:str, username:str, pw:str) -> None:
        self.driver = None
        auth = (username, pw)
        try:
            self.driver = GraphDatabase.driver(uri = uri, auth = auth)
        except Exception as e:
            print("Error creating the driver: ", e)

    def close(self):
        """Close the connection to the kubernetes driver"""
        if self.driver is not None:
            self.driver.close()
        print("neo4j driver closed")
        
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
            print("Query failed:", e)
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
                print("All data has been dropped from the database.")
            except Exception as e:
                print("Failed to drop all data:", e)
        else:
            print("Operation canceled.")

    def create_datagraph(self, file_path: str):
        """Create the datagraph by executing queries from a file."""
        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return
        
        try:
            with open(file_path, 'r') as file:
                queries = file.read()
            
            # Split queries by semicolon and execute each query
            for query in queries.split(';'):
                query = query.strip()
                if query:  # Skip empty queries
                    self.query(query)
            print("Datagraph created successfully.")
        except Exception as e:
            print("Failed to create datagraph:", e)

    def get_services(self) -> list:
        """Return all the kubernetes services in the cluster"""
        result = self.query("MATCH (s:Service) RETURN s.name")
        services = [record["s.name"] for record in result] if result else []
        return services
    
    def get_connected_services(self, service: str) -> list:
        """Return all the connected services to the service"""
        query = "MATCH (s:Service {name: $service_name})-[:CALLS]->(c:Service) RETURN c.name"
        params = {"service_name": service}
        result = self.query(query, params)
        connected_services = [record["c.name"] for record in result] if result else []
        return connected_services
    
    def get_dependencies(self, service: str) -> list:
        query = """
        MATCH (s:Service {name: $service_name})-[:USES]->(dependency)
        RETURN dependency.name AS dependencyName, labels(dependency)[0] AS dependencyType
        """
        params = {"service_name": service}
        result = self.query(query, params)
        dependencies = [{"serviceName" : record["dependencyName"], "serviceType" : record["dependencyType"]} for record in result] if result else []
        return dependencies
    
    def get_service_summary(self, service: str) -> str:
        """
        Generates a summary of a given service, including the services it calls and its dependencies (for LLM purposes).
        """
        connected_services = self.get_connected_services(service)
        dependencies = self.get_dependencies(service)

        summary = f"The service {service} "
        if len(connected_services) > 0:
            summary += f"calls {len(connected_services)} services: {', '.join(connected_services)}."
        else:
            summary += f"doesn't call any service."

        if len(dependencies) > 0:
            summary += f" It has the following {len(dependencies)} dependencies: " + ", ".join([f"{dep['serviceName']} ({dep['serviceType']})" for dep in dependencies]) + "."

        return summary