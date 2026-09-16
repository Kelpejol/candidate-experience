from pydantic import BaseModel


class KbQueryRequest(BaseModel):
    question: str
    k: int = 3
    # The campaign the caller is asking about (its name). Resolved to an active
    # KB scope server-side; unknown/inactive falls back to general content.
    campaign: str | None = None


class KbAnswerResponse(BaseModel):
    answer_available: bool
    content: list[str] | None = None
    sources: list[str] = []
    best_distance: float | None = None


class CampaignResolveRequest(BaseModel):
    name: str


class CampaignResolveResponse(BaseModel):
    # found | inactive | ambiguous | did_you_mean | tool_not_campaign | not_found
    status: str
    # canonical campaign name (for the agent to confirm), when a single match
    # resolved; the platform's name when status is tool_not_campaign
    name: str | None = None
    # names to read back to the caller: the campaigns that matched (ambiguous)
    # or the near-misses to confirm (did_you_mean)
    options: list[str] = []
    # current campaigns, sent only when nothing matched, so the agent can offer
    # real choices instead of dead-ending the call
    available: list[str] = []
    available_count: int = 0


class CollectRequest(BaseModel):
    field: str          # what is being collected, e.g. "email"
    value: str          # the caller-provided, echo-back-confirmed value
    conversation_id: str | None = None
    campaign: str | None = None


class CollectResponse(BaseModel):
    saved: bool
    id: str
