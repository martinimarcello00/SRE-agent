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
        if self.driver is not None:
            self.driver.close()
        print("neo4j driver closed")
        
    def query(self, query, parameters=None, db=None):
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

