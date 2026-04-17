import asyncio
import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from shopifyseo.dashboard_actions import AI_JOBS, AI_JOBS_LOCK, consume_job_events

router = APIRouter(prefix="/api", tags=["ai-stream"])


@router.get("/ai-stream")
async def ai_stream(job_id: str = Query(...)):
    """SSE endpoint that streams AI generation events for a specific job."""

    async def event_generator():
        yield _sse_encode({"type": "connected", "job_id": job_id})

        while True:
            with AI_JOBS_LOCK:
                state = AI_JOBS.get(job_id)

            if state is None:
                yield _sse_encode({"type": "error", "message": "Job not found"})
                return

            events = await asyncio.to_thread(consume_job_events, job_id, 1.0)

            for event in events:
                yield _sse_encode(event)
                if event.get("type") in ("done", "error", "cancelled"):
                    return

            if not state.get("running", False) and not events:
                yield _sse_encode({
                    "type": "done",
                    "stage": state.get("stage", "complete"),
                    "stage_label": state.get("stage_label", ""),
                })
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_encode(data: dict) -> str:
    """Encode a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"
