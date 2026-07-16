"""List Zoho Desk agents and teams — the reference for filling in
ROUTING_ASSIGNEE_BY_CATEGORY in app/services/helpdesk_executor_service.py
once the team decides who owns which ticket category.

Run with: python scripts/check_zoho_agents.py
"""

from app.core.config import get_settings
from app.integrations.zoho_desk_client import build_zoho_desk_client

client = build_zoho_desk_client(get_settings())

print("Agents:")
for agent in client.get("/agents").get("data", []):
    print(f"  {agent.get('id')}  {agent.get('firstName')} {agent.get('lastName')}  {agent.get('emailId')}")

print("\nTeams:")
teams = client.get("/teams").get("teams", [])
if not teams:
    print("  (none — single department, no sub-teams)")
for team in teams:
    print(f"  {team}")
