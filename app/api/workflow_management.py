"""Local workflow management only. None of these routes mutate Kubernetes."""
from fastapi import APIRouter, HTTPException, Query

from app.engine.workflow_dependency_modeling import service
from app.shared.schemas.workflow_management import WorkflowEdit, RecordingStart, WorkflowDecision

router = APIRouter(prefix="/management")


def _call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/catalog")
def catalog(namespace: str = Query(..., min_length=1)):
    return service.workflow_editor_catalog(namespace)


@router.get("/workflows")
def list_workflows():
    return service.list_managed_documents()


@router.post("/workflows")
def create_workflow(request: WorkflowEdit):
    if request.expected_version != 0:
        raise HTTPException(status_code=422, detail="A new workflow starts at version zero")
    return _call(service.save_managed_workflow, request)


@router.get("/workflows/{identity}")
def read_workflow(identity: str, version: int | None = Query(None, ge=1)):
    return service.read_managed_document(identity, version)


@router.put("/workflows/{identity}")
def revise_workflow(identity: str, request: WorkflowEdit):
    return _call(service.save_managed_workflow, request, identity)


@router.post("/workflows/{identity}/delete")
def delete_workflow(identity: str, request: WorkflowDecision):
    return _call(service.delete_managed_workflow, identity, request.expected_version, request.actor)


@router.get("/workflows/{identity}/history")
def history(identity: str):
    return service.managed_workflow_history(identity)


@router.get("/workflows/{identity}/graph")
def reviewed_graph(identity: str, version: int | None = Query(None, ge=1)):
    document = service.read_managed_document(identity, version)
    if not document.get("reviewed") or document.get("deleted"):
        raise HTTPException(status_code=409, detail="Save a reviewed graph before exporting it")
    return {"graph": document["graph"], "version": document["version"], "basis": "declared",
            "experimentallyValidated": False, "limits": document["limits"],
            "digest": service.managed_workflow_digest(document)}


@router.get("/recordings")
def recordings():
    return service.list_managed_documents("recording")


@router.post("/recordings")
def start_recording(request: RecordingStart):
    return _call(service.start_workflow_recording, request)


@router.get("/recordings/{identity}")
def recording(identity: str):
    return service.read_managed_document(identity, kind="recording")


@router.post("/recordings/{identity}/stop")
def stop_recording(identity: str):
    return _call(service.stop_workflow_recording, identity)
