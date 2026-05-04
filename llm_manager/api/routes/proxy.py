from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm_manager.api.dependencies import get_service
from llm_manager.services.request_router import RequestRouter

router = APIRouter()


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    svc: RequestRouter = Depends(get_service(RequestRouter)),
):
    body = await request.json()
    model = body.get("model", "")

    stream = body.get("stream", False)
    if stream:
        return StreamingResponse(
            svc.route_streaming(model, "/v1/chat/completions", body),
            media_type="text/event-stream",
        )

    try:
        response = await svc.route_request(model, "/v1/chat/completions", "POST", body)
        return response.json()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/completions")
async def completions(
    request: Request,
    svc: RequestRouter = Depends(get_service(RequestRouter)),
):
    body = await request.json()
    model = body.get("model", "")

    try:
        response = await svc.route_request(model, "/v1/completions", "POST", body)
        return response.json()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/embeddings")
async def embeddings(
    request: Request,
    svc: RequestRouter = Depends(get_service(RequestRouter)),
):
    body = await request.json()
    model = body.get("model", "")

    try:
        response = await svc.route_request(model, "/v1/embeddings", "POST", body)
        return response.json()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_catch_all(
    path: str,
    request: Request,
    svc: RequestRouter = Depends(get_service(RequestRouter)),
):
    raise HTTPException(status_code=404, detail=f"Endpoint /{path} not supported")
