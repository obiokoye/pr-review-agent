"""Demo mode: scripted model replies for the example diffs in examples/.

The fake picks a reply by looking at which file is in the prompt, so the whole
pipeline (parse -> prompt -> validate -> guard -> render/post) runs for real.
Only the model's answer is canned.
"""

from __future__ import annotations

import json

from .llm import FakeLLM, Message

DEMO_NOTICE = "Demo mode: scripted AI replies"

_SCRIPTS: dict[str, dict] = {
    "app/routes/users.py": {
        "summary": (
            "Adds a user search endpoint and a delete endpoint. The search builds SQL "
            "from user input, and delete has no authentication, so this should not merge yet."
        ),
        "comments": [
            {
                "path": "app/routes/users.py",
                "line": 20,
                "severity": "critical",
                "category": "security",
                "title": "SQL injection in user search",
                "body": (
                    "`q` goes straight into the SQL string. `?q=%' OR '1'='1` returns every user's email. "
                    "Use a placeholder: `db.execute(\"... WHERE name LIKE ?\", (f\"%{q}%\",))`."
                ),
                "suggestion": None,
            },
            {
                "path": "app/routes/users.py",
                "line": 26,
                "severity": "critical",
                "category": "security",
                "title": "Anyone can delete any user",
                "body": "`current_user` is imported but never used, so this endpoint is unauthenticated.",
                "suggestion": (
                    "def delete_user(user_id: int, db: Connection = Depends(get_db), "
                    "user=Depends(current_user)):"
                ),
            },
            {
                "path": "app/routes/users.py",
                "line": 18,
                "severity": "warning",
                "category": "bug",
                "title": "Route is shadowed by /users/{user_id}",
                "body": (
                    "FastAPI matches routes in order, so GET /users/search hits `get_user` first and "
                    "returns 422. Declare the search route above `/users/{user_id}`."
                ),
                "suggestion": None,
            },
            {
                "path": "app/routes/users.py",
                "line": 22,
                "severity": "suggestion",
                "category": "performance",
                "title": "Search has no limit",
                "body": "A one-letter query returns the whole table. Add `LIMIT 50` or a `limit` parameter.",
                "suggestion": None,
            },
            {
                # Deliberately wrong: line 40 is not in the diff. The guard drops it.
                "path": "app/routes/users.py",
                "line": 40,
                "severity": "nit",
                "category": "style",
                "title": "Missing docstring",
                "body": "Consider adding a docstring.",
                "suggestion": None,
            },
        ],
    },
    "app/Http/Controllers/OrderController.php": {
        "summary": (
            "Adds store/update actions and shows customer names on the order list. "
            "Mass assignment and a missing policy check need fixing; the list also has an N+1 query."
        ),
        "comments": [
            {
                "path": "app/Http/Controllers/OrderController.php",
                "line": 23,
                "severity": "critical",
                "category": "security",
                "title": "Mass assignment from $request->all()",
                "body": (
                    "Any field in the request (e.g. `status`, `total`) is written to the order. "
                    "Validate first, or use a Form Request and `$request->validated()`."
                ),
                "suggestion": (
                    "        $order = Order::create($request->validate(['customer_id' => "
                    "'required|exists:customers,id', 'total' => 'required|numeric|min:0']));"
                ),
            },
            {
                "path": "app/Http/Controllers/OrderController.php",
                "line": 12,
                "severity": "warning",
                "category": "performance",
                "title": "N+1 query: customer loaded once per order",
                "body": "The loop below touches `$order->customer` for each of the 20 orders. Eager load it.",
                "suggestion": "        $orders = Order::with('customer')->latest()->paginate(20);",
            },
            {
                "path": "app/Http/Controllers/OrderController.php",
                "line": 30,
                "severity": "warning",
                "category": "security",
                "title": "No authorization on update",
                "body": (
                    "Any signed-in user can change any order's status. Add `$this->authorize('update', $order);` "
                    "and validate `status` against the allowed values."
                ),
                "suggestion": None,
            },
        ],
    },
    "app/utils/slug.py": {
        "summary": (
            "Makes slugify handle accented characters by normalising to ASCII first. "
            "Small, readable change with no issues found."
        ),
        "comments": [],
    },
}


def _reply(messages: list[Message]) -> str:
    prompt = messages[-1]["content"] if messages else ""
    for path, script in _SCRIPTS.items():
        if f"### File: {path}" in prompt:
            return json.dumps(script)
    return json.dumps(
        {
            "summary": f"{DEMO_NOTICE}. Add an API key (e.g. GEMINI_API_KEY) for a real review of this diff.",
            "comments": [],
        }
    )


def demo_llm() -> FakeLLM:
    return FakeLLM(replies=_reply, model="demo (scripted)")
