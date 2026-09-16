"""Thin HTTP client for the Zoho Desk REST API (v1).

Wraps the endpoints the Helpdesk needs: discovering the org and its
departments, listing/fetching tickets and their conversation threads, and
acting on tickets (private comments, draft replies, email replies, field
updates). Auth is delegated to a `ZohoTokenProvider` so access-token
refresh stays out of request code.

Zoho Desk quirks handled here:
- Every request (except listing organizations) must carry an `orgId` header.
- List endpoints return 204 No Content instead of an empty list.
"""

import requests

from app.integrations.zoho_auth import ZohoTokenProvider


class ZohoDeskClient:
    """Authenticated wrapper around a subset of the Zoho Desk v1 REST API."""

    def __init__(
        self,
        base_url: str,
        token_provider: ZohoTokenProvider,
        org_id: str | None = None,
    ):
        """Store the API base URL, token provider, and default org id."""
        self.base_url = base_url.rstrip("/")
        self.token_provider = token_provider
        self.org_id = org_id

    def _headers(self, include_org: bool = True) -> dict[str, str]:
        """Build auth headers; `orgId` is required on all org-scoped endpoints."""
        headers = {
            "Authorization": f"Zoho-oauthtoken {self.token_provider.get_access_token()}",
            "Accept": "application/json",
        }
        if include_org and self.org_id:
            headers["orgId"] = str(self.org_id)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: dict | None = None,
        include_org: bool = True,
    ) -> dict:
        """Issue an authenticated request and return the parsed JSON body.

        Returns `{"data": []}` for 204 No Content, which Zoho Desk uses for
        empty list results. Raises requests.HTTPError on non-2xx responses.
        """
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(include_org=include_org),
            params=params or {},
            json=json,
            timeout=30,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {"data": []}
        return response.json()

    def get(self, path: str, params: dict | None = None) -> dict:
        """Generic GET passthrough, useful for discovery probing."""
        return self._request("GET", path, params=params)

    def post(self, path: str, json: dict) -> dict:
        """Generic POST passthrough, useful for discovery probing."""
        return self._request("POST", path, json=json)

    # Discovery

    def list_organizations(self) -> dict:
        """List Desk organizations the token can access (source of ZOHO_ORG_ID)."""
        return self._request("GET", "/organizations", include_org=False)

    def list_departments(self) -> dict:
        """List departments in the org (source of ZOHO_DEPARTMENT_ID)."""
        return self._request("GET", "/departments", params={"isEnabled": "true"})

    # Tickets

    def list_tickets(
        self,
        department_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        from_index: int = 0,
        include: str = "contacts,assignee",
    ) -> dict:
        """List tickets, newest first, optionally filtered by department/status."""
        params: dict = {
            "limit": limit,
            "from": from_index,
            "include": include,
            "sortBy": "-createdTime",
        }
        if department_id:
            params["departmentId"] = department_id
        if status:
            params["status"] = status
        return self._request("GET", "/tickets", params=params)

    def get_ticket(self, ticket_id: str, include: str = "contacts,assignee") -> dict:
        """Fetch one ticket's full detail."""
        return self._request("GET", f"/tickets/{ticket_id}", params={"include": include})

    def list_ticket_threads(self, ticket_id: str) -> dict:
        """List the conversation threads (messages) on a ticket."""
        return self._request("GET", f"/tickets/{ticket_id}/threads")

    def get_latest_thread(self, ticket_id: str) -> dict:
        """Fetch the most recent thread on a ticket, with full content."""
        return self._request("GET", f"/tickets/{ticket_id}/latestThread")
    
    def get_thread(self, ticket_id: str, thread_id: str) -> dict:
       """Fetch one thread's full detail (list_ticket_threads returns only summaries)."""
       return self._request("GET", f"/tickets/{ticket_id}/threads/{thread_id}")


    # Actions

    def add_comment(self, ticket_id: str, content: str, is_public: bool = False) -> dict:
        """Add a comment to a ticket; private (internal) by default."""
        return self._request(
            "POST",
            f"/tickets/{ticket_id}/comments",
            json={"isPublic": is_public, "content": content, "contentType": "plainText"},
        )

    def create_draft_reply(
        self,
        ticket_id: str,
        content: str,
        from_email_address: str,
        to: str,
        content_type: str = "html",
    ) -> dict:
        """Save an email reply as a draft on the ticket for an officer to review.

        This is the launch-mode path for the PSA's email flow: the AI drafts,
        a human approves and sends from Zoho Desk.
        """
        return self._request(
            "POST",
            f"/tickets/{ticket_id}/draftReply",
            json={
                "channel": "EMAIL",
                "fromEmailAddress": from_email_address,
                "to": to,
                "contentType": content_type,
                "content": content,
            },
        )

    def send_reply(
        self,
        ticket_id: str,
        content: str,
        from_email_address: str,
        to: str,
        content_type: str = "html",
    ) -> dict:
        """Send an email reply on the ticket immediately.

        Reserved for the later auto-reply phase; launch mode is draft-only.
        """
        return self._request(
            "POST",
            f"/tickets/{ticket_id}/sendReply",
            json={
                "channel": "EMAIL",
                "fromEmailAddress": from_email_address,
                "to": to,
                "contentType": content_type,
                "content": content,
            },
        )

    def send_whatsapp_reply(self, ticket_id: str, content: str) -> dict:
        """Send a reply on a WhatsApp-channel ticket.

        UNVERIFIED against live Zoho behavior. create_draft_reply/send_reply
        are hardcoded to "channel": "EMAIL" — Zoho's email-reply endpoints
        cannot be reused for WhatsApp. The best-guess mechanism, based on
        Zoho Desk's comment-based reply model, is that a PUBLIC comment on a
        WhatsApp-channel ticket is what Zoho's own WhatsApp connector
        forwards to the candidate. This has not been confirmed against a
        real WhatsApp ticket (blocked on the Meta/WABA connection landing) —
        verify against Zoho's API docs or a live test conversation before
        ever enabling settings.helpdesk_whatsapp_auto_reply_execute.
        """
        return self.add_comment(ticket_id, content, is_public=True)

    def update_ticket(self, ticket_id: str, fields: dict) -> dict:
        """Patch fields on a ticket (status, priority, assigneeId, custom fields).

        Note: `tags` are NOT a valid field here — a PATCH with `tags` is
        rejected 422. Use `associate_tags` for tags.
        """
        return self._request("PATCH", f"/tickets/{ticket_id}", json=fields)

    def associate_tags(self, ticket_id: str, tags: list[str]) -> dict:
        """Add tags to a ticket via Zoho Desk's dedicated tag endpoint.

        Tags in Zoho Desk are their own objects, not a ticket field — they
        must be attached through /associateTag, not a ticket PATCH.
        """
        return self._request(
            "POST", f"/tickets/{ticket_id}/associateTag", json={"tags": tags}
        )


def build_zoho_desk_client(settings) -> ZohoDeskClient:
    """Build a ZohoDeskClient from app `Settings`.

    Raises RuntimeError naming every missing credential, so discovery
    scripts fail with an actionable message instead of a 401.
    """
    required = {
        "ZOHO_CLIENT_ID": settings.zoho_client_id,
        "ZOHO_CLIENT_SECRET": settings.zoho_client_secret,
        "ZOHO_REFRESH_TOKEN": settings.zoho_refresh_token,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing Zoho settings: {', '.join(missing)}")

    token_provider = ZohoTokenProvider(
        accounts_base_url=settings.zoho_accounts_base_url,
        client_id=settings.zoho_client_id,
        client_secret=settings.zoho_client_secret,
        refresh_token=settings.zoho_refresh_token,
    )
    return ZohoDeskClient(
        base_url=settings.zoho_desk_base_url,
        token_provider=token_provider,
        org_id=settings.zoho_org_id,
    )
