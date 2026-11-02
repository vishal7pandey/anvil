"""Live Confluence smoke test, run by the owner: `uv run python scripts/smoke_confluence.py` from backend/.

Reads credentials from the repo .env. Writes (creates, then updates) one page titled
"Anvil smoke test" in space ANVIL, reads it back and searches for it. Prints ids and URLs only.
"""

import time

from anvil.tools import confluence

page = confluence.confluence_write("ANVIL", "Anvil smoke test", "# Smoke\nWritten by `scripts/smoke_confluence.py`.\n\n- create")
print("create/update ok:", page["url"], "v", page["version"])
page = confluence.confluence_write("ANVIL", "Anvil smoke test", "# Smoke\nUpdated.\n\n```c\nint x;\n```")
print("update ok: v", page["version"])
print("get ok:", confluence.confluence_get(page["id"])["body"][:80])
for _ in range(6):  # CQL search indexes new pages with a few seconds' lag
    hits = [r["title"] for r in confluence.confluence_search('space = ANVIL AND title = "Anvil smoke test"')]
    if hits:
        break
    time.sleep(5)
print("search ok:" if hits else "search EMPTY after 30 s:", hits)
