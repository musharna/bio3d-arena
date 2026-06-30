# bio3d-arena — Project Intelligence

## Multi-agent coordination (agent-mail MCP)

When 2+ Claude sessions work this repo at once, coordinate via the `mcp-agent-mail` MCP (shared local server `http://127.0.0.1:8766`; tools are `mcp__mcp-agent-mail__*`):

- **Session start:** `register_agent(project_key="/home/mjarnold/bio3d-arena", program="claude-code", model="opus")`. The server **auto-assigns your name** (e.g. `CyanSparrow`) and ignores any `name` you pass — use the **returned** name for every later call. Your session stays authenticated, so you don't pass tokens.
- **Before editing files:** `file_reservation_paths(project_key, agent_name, paths=["glob/**"], exclusive=true, ttl_seconds=3600, reason=...)`. Check the returned `conflicts[]` — if non-empty, another agent holds those paths; message them instead of overwriting. Code-path exclusivity is **advisory** (a warning, not a hard lock) — honor it by convention.
- **Communicate:** `send_message(project_key, sender_name, to=[names], subject, body_md)`; read mail with `fetch_inbox(project_key, agent_name)`; `release_file_reservations(...)` when done.
- Server down (`mcp list` shows disconnected)? Restart: `cd ~/mcp_agent_mail && uv run python -m mcp_agent_mail.cli serve-http`.
