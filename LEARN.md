# LEARN: Day 16 for a Laravel developer

Today's agent doesn't chat. It runs inside CI, reads a pull request and writes a code review. Here's how the pieces map to things you already know.

## 1. Project map in Laravel terms

| This project                         | Laravel equivalent                                  |
|--------------------------------------|-----------------------------------------------------|
| `pip install -r requirements.txt`    | `composer install`                                  |
| `reviewer/` package + `__init__.py`  | a namespace like `App\Reviewer`                     |
| `python -m reviewer review ...`      | `php artisan review ...` (an Artisan command)       |
| `cli.py` with `argparse` sub-commands| `app/Console/Commands/*` with signatures            |
| `models.py` (Pydantic)               | Form Request rules + a DTO, but for the *model's* output |
| `github.py` (`httpx.Client`)         | `Http::withToken(...)->baseUrl(...)` macro          |
| `httpx.MockTransport` in tests       | `Http::fake()` + `Http::assertSent()`               |
| `FakeLLM`                            | a fake bound in the container for tests             |
| `action.yml`                         | a reusable deploy script other repos can call       |
| `.github/workflows/*.yml`            | your CI pipeline (like Forge/Envoyer hooks)         |

## 2. Python you'll see here

```python
Severity = Literal["critical", "warning", "suggestion", "nit"]
```
A type that only allows those strings, like a PHP 8.1 backed `enum Severity: string`.

```python
class ReviewComment(BaseModel):
    line: int = Field(ge=1)
```
Pydantic checks types and rules when you call `ReviewComment.model_validate(data)`. It's `['line' => 'required|integer|min:1']`, but it also gives you a typed object back.

```python
@dataclass
class PRReviewer:
    llm: LLM
    max_comments: int = 10
```
Constructor promotion: `public function __construct(public LLM $llm, public int $maxComments = 10) {}`.

```python
valid = {f.path: f.commentable_lines for f in files}
```
A dict comprehension: `collect($files)->mapWithKeys(fn ($f) => [$f->path => $f->commentableLines])`.

`LLM` in `llm.py` is a `Protocol`, which works like a PHP interface that classes satisfy just by having the right methods. `OpenAICompatibleLLM` and `FakeLLM` both have `complete()`, so either can be passed in.

## 3. The key concept: structured output with guards

A chat agent can answer in free text. A review bot can't: GitHub needs a file path and a line number for every inline comment, and a wrong line makes the API call fail. So the pipeline in `agent.py` treats the model like an untrusted API:

1. **Give it coordinates.** `FileDiff.render()` prints each diff line with its *new* line number (`   20 +     sql = f"SELECT ...`). The model copies numbers instead of counting lines.
2. **Demand a shape.** The prompt asks for JSON. `json_mode` is turned on for providers that support it.
3. **Validate.** `LLMReview.model_validate(...)` rejects bad severities, missing fields or non-integer lines.
4. **Retry once.** On a validation error the agent sends the model its own reply plus the error and asks again. A second failure raises `ReviewError` rather than posting junk.
5. **Ground it.** `_guard()` throws away comments on lines that aren't in the diff (`commentable_lines`). This catches hallucinated line numbers before GitHub does.
6. **Decide in code.** `decide_verdict()` looks only at severities. The model never chooses "approve".

Think of steps 3–6 as the Form Request + Policy layer you'd put in front of any input you don't control.

## 4. How it runs in GitHub Actions

`action.yml` is a *composite action*: three steps (set up Python, install, run). GitHub writes the triggering event to a JSON file whose path is in `GITHUB_EVENT_PATH`. `run_action()` in `cli.py` reads the PR number and head commit from it, fetches the diff with `Accept: application/vnd.github.diff`, reviews it and `POST`s to `/repos/{repo}/pulls/{n}/reviews`. If GitHub rejects the inline comments with a 422 (for example, someone pushed while we were reviewing), it posts them in the review body instead.

## 5. Security note

The diff and PR description are written by whoever opened the PR, so treat them like user input. They're wrapped in `<diff>` / `<pr_description>` tags, and the system prompt says not to follow instructions inside them. The real protection is structural, though: the bot can only post `COMMENT` (or `REQUEST_CHANGES` if you opt in), never `APPROVE`, and the verdict comes from code. Also use `pull_request`, not `pull_request_target`, so fork PRs never get your secrets.

## Try this

- Add a `"docs"` category to `Category` in `models.py`, give it an icon in `render.py`, and ask the model to flag public functions without docstrings.
- Write a diff for one of your own Laravel controllers (`git diff > my.diff`) and run `python -m reviewer review my.diff` with a real Gemini key. Compare its comments with what you'd say.
- Set `max_chars` very low in `PRReviewer` and watch the `reviewing chunk 1/2`, `2/2` messages as it splits the PR.
