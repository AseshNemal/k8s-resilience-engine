from fastapi import APIRouter

router = APIRouter(
    prefix="/api/deployments",
    tags=["Deployment Topology Modeling"],
)


@router.get("/sample")
def get_sample_deployment() -> dict:
    return {
        "nodes": [
            {"name": "node-1", "zone": "zone-a"},
            {"name": "node-2", "zone": "zone-a"},
            {"name": "node-3", "zone": "zone-b"},
        ],
        "services": [
            {
                "name": "paymentservice",
                "declaredReplicas": 3,
                "pods": [
                    {
                        "name": "payment-1",
                        "node": "node-1",
                        "ready": True,
                    },
                    {
                        "name": "payment-2",
                        "node": "node-1",
                        "ready": True,
                    },
                    {
                        "name": "payment-3",
                        "node": "node-2",
                        "ready": True,
                    },
                ],
            }
        ],
    }