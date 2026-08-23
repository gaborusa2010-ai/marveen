#!/usr/bin/env python3
"""
Pre-flight check for the outgoing Telegram gate.

The gate rejects a message after it is written, which costs a full round trip
and, worse, tempts you to reword under pressure. Three separate blocks in one
evening came from the same shape: a hyphenated Hungarian suffix after an
abbreviation, where the suffix alone spells an unaccented Hungarian word.
`450 mm-es` reads as `es` (should be `és`), `VAT-ot` reads as `ot` (`öt`),
`CIS-szel` reads as `szel` (`szél`).

Run the draft through this before sending.

Usage:  gate-check.py <file>        or   echo "..." | gate-check.py -
"""
import re
import sys

# Suffixes that collide with an accented Hungarian word when they stand alone.
#
# OBSERVED: the gate actually rejected these, so they are certain.
# SUSPECTED: the same shape, where the accented target is a real standalone
# Hungarian word. Kept deliberately short. An earlier version guessed pairs by
# pattern (`hoz` -> `hoz` with an accent, which is not a word at all) and fired
# on correct text; a checker that cries wolf gets ignored, which is worse than
# no checker.
OBSERVED = {"es": "és", "ot": "öt", "szel": "szél"}
SUSPECTED = {"os": "ős", "ov": "öv", "or": "őr", "ut": "út",
             "el": "él", "er": "ér", "eg": "ég"}
TRAP_SUFFIX = dict(OBSERVED, **SUSPECTED)

HYPHEN_SUFFIX = re.compile(
    r"\b([A-Za-z0-9]+)-(" + "|".join(sorted(TRAP_SUFFIX, key=len, reverse=True)) + r")\b")

CODE_SPAN = re.compile(r"`[^`]*`|```.*?```", re.S)


def _strip_code(text):
    """Blank out code spans, keeping offsets, so quoted examples of the very
    problem being explained do not get reported as the problem."""
    return CODE_SPAN.sub(lambda m: " " * len(m.group()), text)


def check(text):
    problems = []
    prose = _strip_code(text)

    for m in HYPHEN_SUFFIX.finditer(prose):
        stem, suf = m.group(1), m.group(2)
        certainty = "BIZTOS" if suf in OBSERVED else "gyanus"
        problems.append(
            f"[{certainty}] KOTOJELES TOLDALEK: '{m.group()}' -> a kapu a '{suf}' "
            f"reszt onallo szonak nezi es '{TRAP_SUFFIX[suf]}' lehet az elvaras. "
            f"Ird ki a szot, vagy fogalmazd at toldalek nelkul.")

    if "—" in prose or "–" in prose:
        problems.append("GONDOLATJEL (em vagy en dash) a szovegben. Tilos.")

    for m in re.finditer(r"\s--\s", prose):
        problems.append("DUPLA KOTOJEL gondolatjel helyett. Hasznalj kettospontot, "
                        "zarojelet, vagy uj mondatot.")
        break

    if len(text) > 4096:
        problems.append(f"HOSSZ: {len(text)} karakter, a Telegram limit 4096. "
                        f"Darabold, vagy a kuldes csonkolhat.")
    return problems


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    text = sys.stdin.read() if argv[1] == "-" else open(argv[1], encoding="utf-8").read()
    problems = check(text)
    if not problems:
        print(f"OK ({len(text)} karakter, kuldheto)")
        return 0
    print(f"{len(problems)} problema:")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
