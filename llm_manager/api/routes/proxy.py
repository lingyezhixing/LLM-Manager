from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from llm_manager.api.dependencies import get_service
from llm_manager.services.request_router import RequestRouter

router = APIRouter()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "*",
    "Access-Control-Allow-Headers": "*",
}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy_catch_all(
    path: str,
    request: Request,
    svc: RequestRouter = Depends(get_service(RequestRouter)),
):
    # CORS preflight
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=CORS_HEADERS)

    # Parse request body
    model_name: str | None = None
    body: dict | None = None
    raw_body: bytes | None = None

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            model_name = body.get("model")
        except Exception:
            raw_body = await request.body()
    else:
        raw_body = await request.body()

    if not model_name:
        raise HTTPException(status_code=400, detail="请求体中缺少 'model' 字段")

    try:
        result = await svc.route_request(
            model_name_or_alias=model_name,
            path=f"/{path}",
            method=request.method,
            body=body,
            raw_body=raw_body,
            request_headers=dict(request.headers),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if result.is_streaming:
        return StreamingResponse(
            result.stream,
            status_code=result.status_code,
            headers={**result.response_headers, **CORS_HEADERS},
        )

    return Response(
        content=result.content,
        status_code=result.status_code,
        headers={**result.response_headers, **CORS_HEADERS},
    )
