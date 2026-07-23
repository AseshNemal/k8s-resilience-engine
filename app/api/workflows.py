from fastapi import APIRouter

router = APIRouter(
    prefix="/api/workflows",
    tags=["Workflow Dependency Modeling"],
)


@router.get("/sample")
def get_sample_workflow() -> dict:
    return {
        "workflowId": "checkout",
        "criticality": 0.9,
        "mandatoryServices": [
            "cartservice",
            "checkoutservice",
            "paymentservice",
        ],
        "optionalServices": [
            "emailservice",
        ],
        "edges": [
            {
                "from": "frontend",
                "to": "cartservice",
            },
            {
                "from": "cartservice",
                "to": "checkoutservice",
            },
            {
                "from": "checkoutservice",
                "to": "paymentservice",
            },
        ],
    }