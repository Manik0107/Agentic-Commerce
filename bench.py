import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agentic_commerce import db, normalize  # noqa: E402

REPORT_PATH = Path(__file__).parent / "reports" / "benchmark_report.json"
REPORT_PATH.parent.mkdir(exist_ok=True)

# (attribute, raw merchant string, expected canonical)
LABELS = [
    # ── category: in the synonym table
    ("category", "Kurti - Ladies", "kurta"),
    ("category", "kurtha", "kurta"),
    ("category", "STRAIGHT KURTA", "kurta"),
    ("category", "anarkali kurta", "kurta"),
    ("category", "kurta top", "kurta"),
    ("category", "ladies kurti", "kurta"),
    ("category", "sarees", "saree"),
    ("category", "sari", "saree"),
    ("category", "seree", "saree"),
    ("category", "silk saree", "saree"),
    ("category", "lehnga choli", "lehenga"),
    ("category", "ghagra", "lehenga"),
    ("category", "chaniya choli", "lehenga"),
    ("category", "lehanga", "lehenga"),
    ("category", "punjabi suit", "salwar_suit"),
    ("category", "salwar kameez", "salwar_suit"),
    ("category", "churidar suit", "salwar_suit"),
    ("category", "suit set", "salwar_suit"),
    ("category", "duppata", "dupatta"),
    ("category", "chunni", "dupatta"),
    ("category", "odhni", "dupatta"),
    ("category", "mens sherwani", "sherwani"),
    ("category", "shervani", "sherwani"),
    ("category", "modi jacket", "nehru_jacket"),
    ("category", "nehru coat", "nehru_jacket"),
    ("category", "koti", "nehru_jacket"),
    ("category", "plazo", "palazzo"),
    ("category", "palazzo pants", "palazzo"),
    ("category", "blowse", "blouse"),
    ("category", "choli", "blouse"),
    ("category", "shawl", "stole"),
    ("category", "scarf", "stole"),
    # ── category: nowhere in the synonym table (the real test)
    ("category", "ethnic wear top", "kurta"),
    ("category", "ladies long top ethnic", "kurta"),
    ("category", "festive drape", "saree"),
    ("category", "nine yard traditional wear", "saree"),
    ("category", "bottom wear flared", "palazzo"),
    ("category", "wide leg ethnic trouser", "palazzo"),
    ("category", "gents ethnic long coat", "sherwani"),
    ("category", "groom wear full length", "sherwani"),
    ("category", "two piece ladies set", "salwar_suit"),
    ("category", "bridal skirt set", "lehenga"),
    ("category", "shoulder cloth", "dupatta"),
    ("category", "waistcoat ethnic sleeveless", "nehru_jacket"),
    ("category", "fitted short top for drape", "blouse"),
    ("category", "neck wrap winter", "stole"),
    # ── category: genuinely unmappable, must return unknown
    ("category", "assorted festive items", "unknown"),
    ("category", "gift card", "unknown"),
    ("category", "clearance lot 42", "unknown"),
    ("category", "misc", "unknown"),
    # ── colour: family names and clean shades
    ("color", "navy", "blue"),
    ("color", "royal", "blue"),
    ("color", "teal", "blue"),
    ("color", "indigo", "blue"),
    ("color", "sky", "blue"),
    ("color", "maroon", "red"),
    ("color", "rust", "red"),
    ("color", "wine", "red"),
    ("color", "crimson", "red"),
    ("color", "cherry", "red"),
    ("color", "olive", "green"),
    ("color", "bottle", "green"),
    ("color", "emerald", "green"),
    ("color", "mint", "green"),
    ("color", "mustard", "yellow"),
    ("color", "lemon", "yellow"),
    ("color", "gold", "yellow"),
    ("color", "ochre", "yellow"),
    ("color", "fuchsia", "pink"),
    ("color", "rose", "pink"),
    ("color", "blush", "pink"),
    ("color", "lavender", "purple"),
    ("color", "mauve", "purple"),
    ("color", "plum", "purple"),
    ("color", "charcoal", "black"),
    ("color", "jet", "black"),
    ("color", "ivory", "white"),
    ("color", "off white", "white"),
    ("color", "cream", "white"),
    ("color", "beige", "brown"),
    ("color", "camel", "brown"),
    ("color", "coffee", "brown"),
    ("color", "saffron", "orange"),
    ("color", "coral", "orange"),
    ("color", "slate", "grey"),
    ("color", "silver", "grey"),
    # ── colour: misspelt as merchants actually type them
    ("color", "Navy Blu", "blue"),
    ("color", "Maroom", "red"),
    ("color", "gray", "grey"),
    ("color", "offwhite", "white"),
    ("color", "nevy", "blue"),
    ("color", "wht", "white"),
    # ── colour: nowhere in the table
    ("color", "peacock", "blue"),
    ("color", "sea green", "green"),
    ("color", "dusty rose", "pink"),
    ("color", "midnight", "blue"),
    ("color", "burnt sienna", "brown"),
    ("color", "aubergine", "purple"),
    ("color", "gunmetal", "grey"),
    ("color", "champagne", "yellow"),
    ("color", "brick", "red"),
    ("color", "forest", "green"),
    ("color", "powder blue", "blue"),
    ("color", "jet black shimmer", "black"),
    # ── colour: unmappable
    ("color", "multicolour", "unknown"),
    ("color", "assorted", "unknown"),
    ("color", "as per image", "unknown"),
    # ── material
    ("material", "Cotton Blend", "cotton"),
    ("material", "pure cotton", "cotton"),
    ("material", "cotton 100%", "cotton"),
    ("material", "handloom cotton", "cotton"),
    ("material", "cottn", "cotton"),
    ("material", "kora cotton", "cotton"),
    ("material", "Raw Silk", "silk"),
    ("material", "art silk", "silk"),
    ("material", "Banarasi Silk", "silk"),
    ("material", "silk blend", "silk"),
    ("material", "tussar", "silk"),
    ("material", "Georgett", "georgette"),
    ("material", "faux georgette", "georgette"),
    ("material", "viscose", "rayon"),
    ("material", "viscose rayon", "rayon"),
    ("material", "poly blend", "polyester"),
    ("material", "polyster", "polyester"),
    ("material", "Linen Blend", "linen"),
    ("material", "pure linen", "linen"),
    ("material", "velvet", "velvet"),
    ("material", "wool", "wool"),
    ("material", "denim", "denim"),
    ("material", "chiffon", "chiffon"),
    # ── material: nowhere in the table
    ("material", "chanderi", "cotton"),
    ("material", "brocade", "silk"),
    ("material", "khadi", "cotton"),
    ("material", "crepe", "polyester"),
    ("material", "organza", "silk"),
    ("material", "net fabric", "polyester"),
    # ── material: unmappable
    ("material", "mixed fabric", "unknown"),
    ("material", "see description", "unknown"),
]

BANDS = [
    ("0.90 - 1.00  (auto-accept)", 0.90, 1.01),
    ("0.60 - 0.90  (flag for review)", 0.60, 0.90),
    ("0.00 - 0.60  (rejected -> unknown)", 0.0, 0.60),
]


def run(use_llm: bool = True) -> dict:
    db.init()
    by_attribute: dict[str, list] = {}
    for attribute, raw, expected in LABELS:
        by_attribute.setdefault(attribute, []).append((raw, expected))

    scored = []
    for attribute, cases in by_attribute.items():
        results = normalize.normalise(attribute, [raw for raw, _ in cases], use_llm=use_llm)
        for raw, expected in cases:
            got = results[raw]
            scored.append(
                {
                    "attribute": attribute,
                    "raw": raw,
                    "expected": expected,
                    "got": got["canonical"],
                    "confidence": got["confidence"],
                    "source": got["source"],
                    "correct": got["canonical"] == expected,
                }
            )
    return {"cases": scored, "llm": normalize._get_llm().stats() if use_llm else None}


def report(outcome: dict) -> None:
    cases = outcome["cases"]
    total = len(cases)
    correct = sum(c["correct"] for c in cases)

    print(f"\n{'=' * 68}\nNORMALISATION BENCHMARK — {total} labelled cases\n{'=' * 68}")
    print(f"\nOverall accuracy   {correct}/{total} = {correct / total:.1%}")

    print(f"\n{'per attribute':<22}{'n':>5}{'correct':>10}{'accuracy':>11}")
    print("-" * 48)
    for attribute in sorted({c["attribute"] for c in cases}):
        subset = [c for c in cases if c["attribute"] == attribute]
        hits = sum(c["correct"] for c in subset)
        print(f"{attribute:<22}{len(subset):>5}{hits:>10}{hits / len(subset):>10.1%}")

    print(f"\n{'per source tier':<22}{'n':>5}{'correct':>10}{'accuracy':>11}")
    print("-" * 48)
    for source in sorted({c["source"] for c in cases}):
        subset = [c for c in cases if c["source"] == source]
        hits = sum(c["correct"] for c in subset)
        print(f"{source:<22}{len(subset):>5}{hits:>10}{hits / len(subset):>10.1%}")

    # The claim under test: does a higher self-reported confidence actually mean a
    # higher chance of being right? If these rows are not ordered, the score is noise
    # and the auto-accept threshold is not defensible.
    print(f"\n{'model_confidence band':<36}{'n':>5}{'empirical':>12}")
    print("-" * 55)
    for label, low, high in BANDS:
        subset = [c for c in cases if low <= c["confidence"] < high]
        if not subset:
            print(f"{label:<36}{0:>5}{'—':>12}")
            continue
        hits = sum(c["correct"] for c in subset)
        print(f"{label:<36}{len(subset):>5}{hits / len(subset):>11.1%}")

    wrong = [c for c in cases if not c["correct"]]
    if wrong:
        print(f"\n{len(wrong)} misses (the honest exception list):")
        print(f"  {'attribute':<10}{'raw':<28}{'expected':<14}{'got':<14}{'conf':>6}  source")
        for c in sorted(wrong, key=lambda c: -c["confidence"]):
            print(
                f"  {c['attribute']:<10}{c['raw'][:26]:<28}{c['expected']:<14}"
                f"{c['got']:<14}{c['confidence']:>6.2f}  {c['source']}"
            )

    if outcome["llm"]:
        stats = outcome["llm"]
        print(
            f"\nGemini usage: {stats['requests']} requests, "
            f"{stats['input_tokens']} in / {stats['output_tokens']} out tokens, "
            f"cost ${stats['cost_usd']:.2f} (free tier)"
        )

    with open(REPORT_PATH, "w") as f:
        json.dump(
            {
                "total": total,
                "correct": correct,
                "accuracy": round(correct / total, 4),
                **outcome,
            },
            f,
            indent=2,
        )
    print(f"\nwritten to {REPORT_PATH}")


if __name__ == "__main__":
    report(run(use_llm="--offline" not in sys.argv))
