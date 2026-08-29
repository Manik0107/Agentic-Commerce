import difflib
import re

from pydantic import BaseModel, Field

from . import db, taxonomy
from .llm import LLM, LLMUnavailable

# PRD 6.2 confidence bands.
AUTO_ACCEPT = 0.90
REVIEW_FLOOR = 0.60

_llm: LLM | None = None


def _get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM()
    return _llm


class Normalised(BaseModel):
    """One attribute value, as Gemini is asked to return it."""

    raw: str = Field(description="the merchant string, echoed back verbatim")
    canonical: str = Field(description="a value from the allowed list, or 'unknown'")
    shade: str = Field(default="", description="for colours: the specific shade, else ''")
    confidence: float = Field(description="0.0-1.0 self-reported certainty")


class NormalisedBatch(BaseModel):
    results: list[Normalised]


_PROMPT_PREFIX = """You normalise Indian ethnic-wear catalog vocabulary into a fixed taxonomy.

Rules:
- `canonical` MUST be one of the allowed values listed below, or exactly "unknown".
- Never invent a value that is not in the list. "unknown" is a correct answer.
- For colours, `canonical` is the colour FAMILY and `shade` is the specific shade
  named by the merchant (e.g. "Navy Blu" -> canonical "blue", shade "navy").
  If the merchant named only a family, leave `shade` empty.
- `confidence` is your honest certainty. Use a low value when you are guessing from
  weak evidence; a wrong high-confidence answer is worse than an honest low one.
- Echo `raw` back exactly as given, including its original casing.

Allowed values:
  category: {categories}
  color:    {colors}
  material: {materials}
"""


def _prefix() -> str:
    """Byte-identical across every call, so Gemini's prompt cache can hit."""
    return _PROMPT_PREFIX.format(
        categories=", ".join(taxonomy.CATEGORIES),
        colors=", ".join(taxonomy.COLOR_FAMILIES),
        materials=", ".join(taxonomy.MATERIALS),
    )


def _band(canonical: str, shade: str, confidence: float, raw: str, source: str) -> dict:
    """Apply the PRD 6.2 confidence bands; below the floor, canonical is unknown."""
    if confidence < REVIEW_FLOOR or not canonical:
        return {
            "canonical": "unknown",
            "shade": "",
            "confidence": confidence,
            "raw": raw,
            "source": source,
            "needs_review": True,
        }
    return {
        "canonical": canonical,
        "shade": shade,
        "confidence": confidence,
        "raw": raw,
        "source": source,
        "needs_review": confidence < AUTO_ACCEPT,
    }


def _shade_from_raw(raw: str) -> str:
    """Recover the shade from the raw string.

    The family lookup alone is lossy: "Navy Blu" resolves to `blue` and the shade is
    gone. Matched fuzzily because merchants misspell shades ("Maroom" -> maroon).
    """
    shades = list(taxonomy.SHADE_OF)
    for token in re.findall(r"[a-z]+", raw.lower()):
        if token in taxonomy.SHADE_OF:
            return token
        if m := difflib.get_close_matches(token, shades, n=1, cutoff=0.8):
            return m[0]
    return ""


def _exact(attribute: str, raw: str) -> dict | None:
    key = raw.strip().lower()
    canonical = taxonomy.LOOKUPS[attribute].get(key)
    if canonical is None:
        return None
    shade = _shade_from_raw(raw) if attribute == "color" else ""
    return _band(canonical, shade, 1.0, raw, "exact")


def _fuzzy(attribute: str, raw: str) -> dict:
    """Stdlib fallback. SequenceMatcher's ratio is already 0.0-1.0, so it feeds the
    same confidence bands as a model score."""
    key = raw.strip().lower()
    table = taxonomy.LOOKUPS[attribute]
    match = difflib.get_close_matches(key, table, n=1, cutoff=REVIEW_FLOOR)
    if not match:
        return _band("unknown", "", 0.0, raw, "difflib")
    hit = match[0]
    ratio = difflib.SequenceMatcher(None, key, hit).ratio()
    shade = _shade_from_raw(raw) if attribute == "color" else ""
    return _band(table[hit], shade, ratio, raw, "difflib")


def _ask_gemini(attribute: str, raws: list[str]) -> dict[str, dict]:
    """One request for up to `len(raws)` strings. Returns {raw: banded result}.

    Values the model omits, or answers outside the taxonomy, are left out so the
    caller falls through to difflib for them.
    """
    listing = "\n".join(f"- {r}" for r in raws)
    suffix = (
        f"\nNormalise these {attribute} values. Return one result per input, "
        f"in the same order:\n{listing}\n"
    )
    batch = _get_llm().call(_prefix(), suffix, NormalisedBatch)

    out: dict[str, dict] = {}
    valid = taxonomy.VALID[attribute]
    for item in batch.results:
        if item.raw not in raws:
            continue
        canonical = item.canonical.strip().lower().replace(" ", "_")
        if canonical not in valid and canonical != "unknown":
            continue
        confidence = max(0.0, min(1.0, float(item.confidence)))
        shade = item.shade.strip().lower()
        if attribute == "color" and shade in taxonomy.VALID["color"]:
            shade = _shade_from_raw(item.raw)  # model returned a family, not a shade
        out[item.raw] = _band(canonical, shade, confidence, item.raw, "gemini")
    return out


def normalise(attribute: str, raws: list[str], use_llm: bool = True) -> dict[str, dict]:
    """Normalise many raw values of one attribute. Returns {raw: result}.

    Batched and de-duplicated: variants share most of their attribute strings, and
    cached values cost nothing on a re-sync.
    """
    conn = db.connect()
    results: dict[str, dict] = {}
    pending: list[str] = []

    for raw in dict.fromkeys(raws):  # de-duplicate, preserve order
        row = conn.execute(
            "SELECT canonical, shade, confidence, source FROM norm_cache "
            "WHERE attribute = ? AND raw = ?",
            (attribute, raw),
        ).fetchone()
        if row:
            results[raw] = _band(
                row["canonical"],
                row["shade"] or "",
                row["confidence"],
                raw,
                row["source"],
            )
            continue
        if hit := _exact(attribute, raw):
            results[raw] = hit
            pending.append(raw)  # still worth caching
            continue
        pending.append(raw)

    unknown = [r for r in pending if r not in results]
    if unknown and use_llm:
        try:
            results.update(_ask_gemini(attribute, unknown))
        except LLMUnavailable as e:
            print(f"  [normalise] Gemini unavailable ({e}); falling back to difflib")
        except Exception as e:  # a malformed response is not fatal
            print(f"  [normalise] Gemini error ({type(e).__name__}: {e}); using difflib")

    for raw in unknown:
        results.setdefault(raw, _fuzzy(attribute, raw))

    for raw in pending:
        r = results[raw]
        # Do not cache a weak fuzzy guess: it is a symptom of Gemini being
        # unreachable, and caching it would stop the next sync ever retrying.
        if r["source"] == "difflib" and r["confidence"] < AUTO_ACCEPT:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO norm_cache "
            "(attribute, raw, canonical, shade, confidence, source) VALUES (?,?,?,?,?,?)",
            (attribute, raw, r["canonical"], r["shade"], r["confidence"], r["source"]),
        )
    conn.close()
    return results
