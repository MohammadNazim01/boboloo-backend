import re
import spacy
from spacy.matcher import PhraseMatcher

from app.services.analytics_engine.vocabulary_service import process_vocabulary
from app.services.analytics_engine.cmi_service import score_cmi
from app.services.analytics_engine.sentiment_service import analyze_sentiment

# ── shared NLP model (loaded once at import time) ──────────────────────────
_nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
if "sentencizer" not in _nlp.pipe_names:
    _nlp.add_pipe("sentencizer")

# Curiosity phrase matcher (multi-word only; single words handled by word count)
_curiosity_matcher = PhraseMatcher(_nlp.vocab, attr="LOWER")
_curiosity_matcher.add(
    "CURIOSITY",
    [_nlp.make_doc(p) for p in ("what if", "i wonder", "suppose", "imagine")],
)

_WHITESPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", text.replace("’", "'")).strip()


# ── public API ─────────────────────────────────────────────────────────────

def generate_analytics(text_list: list[str], existing_words) -> dict:
    """
    Full child-speech analytics pipeline.

    Single spaCy pass feeds all sub-services; VADER runs on raw text in parallel
    (it needs no spaCy tokens).

    Returns:
        {
          "vocabulary": { TotalWordsCount, UniqueWordsCount, NewWordsCount,
                          UniqueWordsList, NewWordsList, UpdatedVocabulary },
          "cmi":        { curiosity, reasoning, sentence_complexity, cmi,
                          details },
          "sentiment":  { compound, positive, negative, neutral },
        }
    """
    cleaned = [_clean(t) for t in text_list if t and t.strip()]
    if not cleaned:
        return {"vocabulary": {}, "cmi": {}, "sentiment": {}}

    # ── 1. Single NLP pass ──────────────────────────────────────────────────
    docs = list(_nlp.pipe(cleaned, batch_size=32))

    # ── 2. Vocabulary ───────────────────────────────────────────────────────
    vocabulary = process_vocabulary(docs, existing_words)

    # ── 3. CMI (reuses vocab richness — no duplicate token walk needed) ─────
    cmi_result = score_cmi(docs, vocabulary, _curiosity_matcher)

    # ── 4. Sentiment (VADER on raw text, no spaCy tokens needed) ────────────
    sentiment = analyze_sentiment(cleaned)

    return {
        "vocabulary": vocabulary,
        "cmi": {
            "curiosity": cmi_result.curiosity,
            "reasoning": cmi_result.reasoning,
            "sentence_complexity": cmi_result.sentence_complexity,
            "cmi": cmi_result.cmi,
            "details": cmi_result.details,
        },
        "sentiment": sentiment,
    }
