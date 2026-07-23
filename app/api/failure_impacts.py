from fastapi import APIRouter

router = APIRouter(
    prefix="/api/failure-impacts",
    tags=["Failure Impact Prediction"],
)


@router.get("/sample")
def get_sample_failure_impact() -> dict:
    return {
        "workflowId": "checkout",
        "failedNode": "node-1",
        "blastRadius": 60,
        "survivability": 40,
        "failedServices": [
            "paymentservice",
        ],
        "failedWorkflows": [
            "checkout",
        ],
        "rootCauses": [
            {
                "type": "REPLICA_CONCENTRATION",
                "service": "paymentservice",
                "failureDomain": "node-1",
                "concentration": 0.67,
            }
        ],
    }