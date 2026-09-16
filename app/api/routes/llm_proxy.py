"""OpenAI-compatible proxy to the inference gateway.

ElevenLabs can't reach the gateway directly (gpu.idhub.ng is behind Cloudflare,
which blocks ElevenLabs' server-to-server calls). ElevenLabs points its Custom
LLM at THIS route instead — reachable via ngrok, not behind that Cloudflare —
and we forward the request to the gateway *server-side* (the same call that
already works from this machine's curl), streaming the response straight back.

The relay is a byte-for-byte passthrough, so streaming, tool-call deltas, and
the terminating [DONE] all survive untouched — no parsing or reshaping.
"""

import logging

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings

router = APIRouter(prefix="/llm/v1", tags=["LLM Proxy"])


@router.post("/chat/completions")
async def proxy_chat_completions(
    request: Request, authorization: str | None = Header(default=None)
):
    """Forward an OpenAI chat-completions request to the gateway and stream it back."""
    settings = get_settings()

    # Inbound auth: ElevenLabs sends this token; the real gateway key stays server-side.
    if settings.llm_proxy_token and authorization != f"Bearer {settings.llm_proxy_token}":
        raise HTTPException(status_code=401, detail="Invalid proxy token")

    body = await request.body()

    headers = {
        "Authorization": f"Bearer {settings.llm_gateway_key}",
        "Content-Type": "application/json",
    }

    # Bounded timeouts: short connect, and a finite READ gap so a gateway that
    # stalls mid-stream (or never sends [DONE]) can't hang the call forever with
    # dead air. 30s between SSE chunks is generous but not unbounded.
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
    )

    # If the gateway is unreachable, close the client we just opened (the relay's
    # finally never runs since it never starts) and surface a clean 502 rather
    # than leaking the client and 500-ing.
    try:
        upstream = await client.send(
            client.build_request(
                "POST", settings.llm_gateway_chat_url, content=body, headers=headers
            ),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        logging.exception("LLM proxy could not reach the gateway")
        raise HTTPException(status_code=502, detail="Upstream LLM gateway unavailable")

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk  # SSE chunks (incl. tool_calls) forwarded verbatim
        except httpx.HTTPError:
            # A stall/disconnect mid-stream ends the response cleanly; the agent
            # sees a truncated stream rather than a hung connection.
            logging.exception("LLM proxy stream interrupted")
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        relay(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )
