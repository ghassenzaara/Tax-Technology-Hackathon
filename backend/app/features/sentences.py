"""Step 9: one descriptive German sentence per feature (LLM, checked by plain code).

The sentence describes the tax situation in general terms, so it sits close to law text in
vector space. Deterministic checks after the call: not empty, no digit at all, no name of
the profile, within the length caps. One repair retry states the violation; if the answer
is still invalid (or the call fails), a deterministic fallback sentence is used, so every
feature always gets a valid sentence. `write` never raises.
"""

import asyncio
import logging
import re
from typing import Any

from openai import AsyncOpenAI

from app.config import Settings
from app.features.select import term_pattern
from app.llm import call_with_retry
from app.models.features import Feature

log = logging.getLogger(__name__)

_DIGIT = re.compile(r"\d")
GENERIC_SENTENCE = "Die Gesellschaft weist einen steuerlich relevanten Sachverhalt auf."

SYSTEM_PROMPT = """Du schreibst für den Abgleich mit Gesetzestexten genau einen deutschen Satz,
der einen steuerlich relevanten Sachverhalt einer Gesellschaft allgemein beschreibt.

Regeln:
- Höchstens {max_words} Wörter, ein einziger Satz, sachlich wie in einem Gesetz oder Erlass.
- Keine Ziffern und keine Zahlen: keine Beträge, Jahre, Daten, Prozentsätze, Aktenzeichen,
  Paragraphennummern. Normen nur mit ihrem Inhalt umschreiben (zum Beispiel "Verlustabzug
  nach dem Körperschaftsteuergesetz" statt der Paragraphennummer).
- Keine Namen von Gesellschaften, Personen, Orten, Gerichten oder Dateien.
- Nur Fakten aus der Eingabe verwenden, nichts erfinden.
Antworte nur mit dem Satz."""


def sentence_violations(text: str | None, feature: Feature, settings: Settings) -> list[str]:
    """Deterministic checks; an empty list means the sentence is valid."""
    text = (text or "").strip()
    if not text:
        return ["the answer is empty"]
    problems = []
    if _DIGIT.search(text):
        problems.append("it contains digits; numbers, years and paragraph numbers are not allowed")
    names = [t for t in feature.forbidden if term_pattern(t).search(text)]
    if names:
        problems.append(f"it contains names ({', '.join(names)}); describe without names")
    words = len(text.split())
    if words > settings.sentence_max_words:
        problems.append(f"it has {words} words; at most {settings.sentence_max_words} are allowed")
    if len(text) > settings.sentence_max_chars:
        problems.append(f"it has {len(text)} characters; at most {settings.sentence_max_chars}")
    return problems


def fallback_sentence(feature: Feature, settings: Settings) -> str:
    """The feature's deterministic sentence, cut to the caps; generic if still invalid."""
    words = feature.fallback.split()
    text = " ".join(words[: settings.sentence_max_words])
    if len(words) > settings.sentence_max_words:
        text = text.rstrip(",;:") + "."
    text = text[: settings.sentence_max_chars]
    return GENERIC_SENTENCE if sentence_violations(text, feature, settings) else text


def user_message(feature: Feature) -> str:
    return "\n".join(f"{name}: {value}" for name, value in feature.facts.items())


class SentenceWriter:
    """Same prompt for every feature; `slots` caps requests in flight."""

    def __init__(self, settings: Settings, client: AsyncOpenAI, slots: asyncio.Semaphore):
        self.settings = settings
        self.client = client
        self.slots = slots
        self.system_prompt = SYSTEM_PROMPT.format(max_words=settings.sentence_max_words)

    async def write(self, feature: Feature) -> tuple[str, bool]:
        """(sentence, fallback used). Failures stay with this feature."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_message(feature)},
        ]
        try:
            text = await self._ask(messages)
            problems = sentence_violations(text, feature, self.settings)
            if problems:
                repair = [
                    *messages,
                    {"role": "assistant", "content": text or "(keine Antwort)"},
                    {
                        "role": "user",
                        "content": "Der Satz ist ungültig: "
                        + "; ".join(problems)
                        + ". Schreibe ihn neu und halte alle Regeln ein.",
                    },
                ]
                text = await self._ask(repair)
                problems = sentence_violations(text, feature, self.settings)
            if not problems:
                return text.strip(), False
            # Log the reference only: the problems may quote names from the profile.
            log.warning("sentence for %s invalid after repair, fallback used", feature.ref)
        except Exception as exc:  # isolated per feature: fall back, never fail the run
            log.warning(
                "sentence for %s failed (%s), fallback used", feature.ref, type(exc).__name__
            )
        return fallback_sentence(feature, self.settings), True

    async def _ask(self, messages: list[dict[str, Any]]) -> str | None:
        settings = self.settings
        response = await call_with_retry(
            lambda: self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                reasoning_effort=settings.sentence_reasoning_effort,
            ),
            slots=self.slots,
            max_attempts=settings.agent_max_attempts,
            backoff_min_s=settings.agent_backoff_min_s,
            backoff_max_s=settings.agent_backoff_max_s,
        )
        choices = getattr(response, "choices", None) or []
        if not choices or getattr(choices[0].message, "refusal", None):
            return None
        return choices[0].message.content
