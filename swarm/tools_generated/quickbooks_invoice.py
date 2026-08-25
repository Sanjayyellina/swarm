from swarm.tools import tool

@tool("quickbooks_invoice", "Auto-generated stub: records intent for the owner until real integration is configured.",
      {"properties": {"details": {"type": "string"}},
       "required": ["details"]})
def quickbooks_invoice(ctx, details):
    ctx["memory"].take_message("system", "n/a",
        f"QUEUED (quickbooks_invoice): {details}")
    return {"status": "queued_for_owner", "details": details}