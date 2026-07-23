from fastapi import APIRouter

router = APIRouter(
    prefix="/api",
    tags=["System"],
)


@router.get("/health")
def health_check() -> dict:
    return {
        "status": "healthy",
        "system": "K8s Resilience Engine",
        "version": "0.1.0",
    }