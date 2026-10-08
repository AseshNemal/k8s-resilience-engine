import sys
from pathlib import Path

from app.engine.deployment_topology_modeling.manifest_parsing.pipeline import run_manifest_pipeline

def run_demonstration():
    try:
        print("Starting Enterprise YAML Extraction Engine...\n")
        
        # Point the Phase 2 Pipeline directly at the Google Boutique folder
        intents = run_manifest_pipeline('deploy/kubernetes')
        
        print(f"---  Successfully Extracted {len(intents)} Google Microservices! ---")
        
        for intent in intents:
            print(f"\nService: {intent.service_name}")
            print(f"  Target Replicas: {intent.expected_replicas}")
            print(f"  CPU Limit:       {intent.resource_limits.cpu}")
            print(f"  Memory Limit:    {intent.resource_limits.memory}")
            print("-" * 45)
            
    except Exception as e:
        print(f"Extraction Error: {e}")

if __name__ == '__main__':
    run_demonstration()
