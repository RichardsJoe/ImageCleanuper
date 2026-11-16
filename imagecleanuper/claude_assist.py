"""Optional Claude second opinion (pseudocode section 13).

Only photos the local image model is unsure about (Pets, Junk, your categories) are sent, one small
JPEG per photo, and only when you've switched Claude on and added an API key. The key lives in
Windows Credential Manager (never in settings.ini). A monthly spending limit is enforced: when it's
reached you choose to continue with the free local models, raise the limit, or pause; with no answer
in 5 minutes the run continues locally. Your own answers always win over Claude's.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable

from . import log
from .catalog import Catalog
from .settings import Settings

_log = log.get("claude")
SERVICE = "ImageCleanuper"
KEY_NAME = "anthropic_api_key"
KEYS_URL = "https://platform.claude.com/settings/keys"
BILLING_URL = "https://platform.claude.com/settings/billing"
MODELS = {"claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5",
          "claude-haiku-5-5": "Claude Haiku 5.5"}
PRICES = {"claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
          "claude-opus-4-8": (5.0, 25.0), "claude-haiku-5-5": (0.10, 0.50)}     # $ per million tokens in / out
LIMIT_STEPS = (2.0, 5.0, 10.0, 20.0, 50.0)
MAX_SIDE = 1024
FALLBACK_BETA = "server-side-fallback-2026-07-01"


# ---- the key (Windows Credential Manager via keyring) ------------------------------------------
def get_key() -> str | None:
    try:
        import keyring
        return keyring.get_password(SERVICE, KEY_NAME)
    except Exception:             # no credential store available
        return None


def set_key(key: str) -> None:
    import keyring
    keyring.set_password(SERVICE, KEY_NAME, key.strip())


def delete_key() -> None:
    try:
        import keyring
        keyring.delete_password(SERVICE, KEY_NAME)
    except Exception:
        pass


def _client(key: str):
    import anthropic
    return anthropic.Anthropic(api_key=key, max_retries=3, timeout=120.0)


def test_key(key: str, model: str) -> tuple[bool, str]:
    """Check the key works for the chosen model (no photo is sent; costs nothing)."""
    import anthropic
    try:
        _client(key).models.retrieve(model)
        return True, "Key works"
    except anthropic.AuthenticationError:
        return False, "That key wasn't accepted. Copy it again from the Claude Console."
    except anthropic.PermissionDeniedError:
        return False, "The key works but can't use this model. Check the key's workspace in the Console."
    except anthropic.NotFoundError:
        return False, "This model isn't available to your account."
    except anthropic.APIConnectionError:
        return False, "Couldn't reach Claude. Check your internet connection."
    except anthropic.APIStatusError as e:
        return False, f"Claude said: {e.message}"


# ---- spending ----------------------------------------------------------------------------------
def _month() -> str:
    return dt.date.today().strftime("%Y-%m")


def spent_this_month(cat: Catalog) -> float:
    v = cat.scalar("SELECT value FROM app_state WHERE key=?", (f"claude_spend:{_month()}",))
    return float(v or 0)


def _add_spend(cat: Catalog, usd: float) -> None:
    with cat.tx():
        cat.db.execute("INSERT INTO app_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                       "value = CAST(CAST(value AS REAL) + ? AS TEXT)", (f"claude_spend:{_month()}", str(usd), usd))


def cost_of(usage, model: str) -> float:
    pin, pout = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    tokens_in = (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) \
        * 1.25 + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
    return tokens_in * pin / 1e6 + (getattr(usage, "output_tokens", 0) or 0) * pout / 1e6


# ---- what to ask ---------------------------------------------------------------------------------
def _questions(s: Settings) -> dict[str, str]:
    q = {"pet": "Is there a pet (a dog, cat or other animal someone keeps) clearly in this photo?",
         "junk": "Is this junk rather than a keepsake photo: a screenshot, meme, receipt, document, accidental "
                 "shot of the floor or pocket, or a hopelessly blurry picture?"}
    for key, (folder, desc) in s.categories.items():
        q[f"cat:{key}"] = f"Does this photo belong in a folder called '{folder}' ({desc})?"
    return q


def pending(cat: Catalog, s: Settings) -> list[dict]:
    """Photos with unsure checks that neither you nor Claude has answered yet."""
    qs = _questions(s)
    rows = cat.q("""SELECT p.asset_id, p.check_key, a.derived_path, f.orig_path, f.sha256, a.kind, a.cutout_index
                    FROM predictions p JOIN assets a ON a.id = p.asset_id JOIN active_files f ON f.id = a.file_id
                    WHERE p.status = 'review' ORDER BY p.asset_id""")
    done = {(r[0], r[1]) for r in cat.q("SELECT content_key, check_key FROM claude_opinions")}
    out: dict[int, dict] = {}
    for r in rows:
        ck = f"{r['sha256']}:{r['kind']}:{'' if r['cutout_index'] is None else r['cutout_index']}"
        if r["check_key"] not in qs or (ck, r["check_key"]) in done:
            continue
        item = out.setdefault(r["asset_id"], {"asset_id": r["asset_id"], "ck": ck, "checks": [],
                                              "derived_path": r["derived_path"], "orig_path": r["orig_path"]})
        item["checks"].append(r["check_key"])
    return list(out.values())


def _jpeg_b64(path: str) -> str:
    from .analyze.imageio import open_image, upright
    img, info = open_image(path)
    img, _ = upright(img, info.orientation)
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


SYSTEM = ("You help sort a family's personal photo library. For each question about the photo, answer yes or no "
          "only when you are confident; otherwise answer unsure. Judge only what is visible in the photo.")


def ask_one(client, model: str, image_b64: str, checks: list[str], questions: dict[str, str]):
    """One request for one photo. Returns (answers {check: True/False}, response)."""
    listing = "\n".join(f"- {k}: {questions[k]}" for k in checks)
    schema = {
        "type": "object",
        "properties": {"answers": {"type": "array", "items": {
            "type": "object",
            "properties": {"check": {"type": "string", "enum": checks},
                           "answer": {"type": "string", "enum": ["yes", "no", "unsure"]}},
            "required": ["check", "answer"], "additionalProperties": False}}},
        "required": ["answers"], "additionalProperties": False}
    response = client.beta.messages.create(
        model=model,
        max_tokens=2048,
        betas=[FALLBACK_BETA],
        fallbacks="default",                     # a declined request is re-run on Anthropic's recommended model
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": f"Answer each question about this photo:\n{listing}"}]}],
    )
    if response.stop_reason == "refusal":
        return {}, response
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        return {}, response
    return {a["check"]: a["answer"] == "yes" for a in data.get("answers", [])
            if a.get("check") in checks and a.get("answer") in ("yes", "no")}, response


@dataclass
class ClaudeResult:
    photos: int = 0
    answered: int = 0
    unsure: int = 0
    spent: float = 0.0
    left: int = 0
    stopped: str = ""                       # "" | "limit_local" | "paused" | "error: ..."
    errors: list[str] = field(default_factory=list)


class Paused(Exception):
    pass


def run(cat: Catalog, s: Settings, derived_dir: str, progress: Callable[..., None] | None = None,
        ask_limit: Callable[[float, float, int, int], str] | None = None, client=None, key: str | None = None
        ) -> ClaudeResult:
    """Second opinions for unsure photos. ask_limit(spent, limit, done, left) returns
    'local' (continue without Claude), 'raise' (limit + $5) or 'pause'."""
    import anthropic
    progress = progress or (lambda *a: None)
    res = ClaudeResult()
    if not s.claude_enabled:
        return res
    key = key or get_key()
    if client is None:
        if not key:
            res.stopped = "error: no API key (Settings › Claude)"
            return res
        client = _client(key)
    todo = pending(cat, s)
    qs = _questions(s)
    limit = s.claude_monthly_limit_usd
    for i, item in enumerate(todo):
        spent = spent_this_month(cat)
        if spent >= limit:
            choice = ask_limit(spent, limit, res.photos, len(todo) - i) if ask_limit else "local"
            if choice == "raise":
                limit += 5.0
                s.claude_monthly_limit_usd = limit
            elif choice == "pause":
                res.left = len(todo) - i
                res.stopped = "paused"
                raise Paused()
            else:
                res.left = len(todo) - i
                res.stopped = "limit_local"
                break
        progress("Asking Claude about unsure photos", i + 1, len(todo))
        path = os.path.join(derived_dir, item["derived_path"]) if item["derived_path"] else item["orig_path"]
        try:
            answers, response = ask_one(client, s.claude_model, _jpeg_b64(path), item["checks"], qs)
        except anthropic.AuthenticationError:
            res.stopped = "error: the API key was rejected"
            break
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            res.errors.append(str(e))
            if len(res.errors) >= 5:
                res.stopped = "error: Claude isn't responding; the rest stay in Needs review"
                break
            continue
        except anthropic.APIStatusError as e:
            res.errors.append(e.message)
            continue
        except OSError as e:
            res.errors.append(str(e))
            continue
        usd = cost_of(response.usage, getattr(response, "model", s.claude_model) or s.claude_model)
        _add_spend(cat, usd)
        res.spent += usd
        res.photos += 1
        now = time.time()
        with cat.tx():
            for check, value in answers.items():
                cat.db.execute("INSERT OR REPLACE INTO claude_opinions(content_key, check_key, value, model, "
                               "created_at) VALUES (?,?,?,?,?)", (item["ck"], check, int(value), s.claude_model, now))
                cat.db.execute("UPDATE predictions SET status=? WHERE asset_id=? AND check_key=? AND status='review'",
                               ("yes" if value else "no", item["asset_id"], check))
        res.answered += len(answers)
        res.unsure += len(item["checks"]) - len(answers)
    _log.info("Claude: %s photos, %s answers, %s unsure, $%.3f", res.photos, res.answered, res.unsure, res.spent)
    return res
