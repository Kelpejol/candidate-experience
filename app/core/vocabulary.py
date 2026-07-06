ALLOWED_TOOLS = {"FOT", "Scholastica"}

def is_tool_allowed(tool_name: str) -> bool:
    return tool_name in ALLOWED_TOOLS