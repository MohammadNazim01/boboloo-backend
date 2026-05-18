from dataclasses import dataclass, asdict
from typing import Dict, List

# True speech disfluencies — NOT reasoning connectors (those are signals, not noise)
CMI_FILLER_WORDS = {
    "um", "uh", "hmm", "uhh", "umm",
    "like", "so", "well", "okay", "ok", "yeah",
    "wait", "actually", "basically", "really", "very", "super",
    "kinda", "sorta", "huh",
}

# Single-word curiosity signals
_CURIOSITY_SINGLES = {"what", "why", "how", "when", "where"}

# Single-word reasoning signals
_REASONING_SINGLES = {
    "because", "since", "therefore", "however", "although",
    "but", "also", "additionally", "moreover", "first", "then", "finally",
}


@dataclass
class CMIResult:
    curiosity: float       # 0–100 score
    reasoning: float       # 0–100 score
    sentence_complexity: float   # mean meaningful tokens per sentence
    cmi: float             # 0–100 composite index
    details: Dict


def score_cmi(docs, vocab_result: dict, curiosity_phrase_matcher) -> CMIResult:
    """
    Scores Conversational Maturity Index from pre-computed spaCy docs.

    Vocab richness is taken from the vocabulary pipeline result rather than
    recomputed here, so token filtering stays consistent across both services.
    """
    total_sentences = 0
    total_tokens = 0
    meaningful_tokens = 0
    filler_count = 0
    curiosity_hits = 0
    reasoning_hits = 0

    for doc in docs:
        sents = list(doc.sents)
        total_sentences += max(len(sents), 1)

        for token in doc:
            if not token.is_alpha:
                continue
            total_tokens += 1
            lower = token.text.lower()
            if lower in CMI_FILLER_WORDS:
                filler_count += 1
                continue
            if token.is_stop:
                continue
            meaningful_tokens += 1

        doc_lower = doc.text.lower()

        for w in _CURIOSITY_SINGLES:
            curiosity_hits += _count_word(doc_lower, w)
        curiosity_hits += len(curiosity_phrase_matcher(doc))

        for w in _REASONING_SINGLES:
            reasoning_hits += _count_word(doc_lower, w)

    vocab_richness = vocab_result["UniqueWordsCount"] / max(vocab_result["TotalWordsCount"], 1)
    curiosity_score = curiosity_hits / max(total_sentences, 1)
    reasoning_score = reasoning_hits / max(total_sentences, 1)
    sentence_complexity = meaningful_tokens / max(total_sentences, 1)
    filler_penalty = min(filler_count / max(total_tokens, 1), 1.0) * 10

    cmi = (
        30 * min(curiosity_score / 2, 1.0)
        + 30 * min(reasoning_score / 2, 1.0)
        + 25 * min(vocab_richness * 2, 1.0)
        + 15 * min(sentence_complexity / 10, 1.0)
        - filler_penalty
    )
    cmi = max(0.0, min(100.0, cmi))

    return CMIResult(
        curiosity=round(min(curiosity_score * 100, 100), 2),
        reasoning=round(min(reasoning_score * 100, 100), 2),
        sentence_complexity=round(sentence_complexity, 2),
        cmi=round(cmi, 2),
        details={
            "total_sentences": total_sentences,
            "meaningful_tokens": meaningful_tokens,
            "filler_count": filler_count,
            "curiosity_hits": curiosity_hits,
            "reasoning_hits": reasoning_hits,
        },
    )


def _count_word(text: str, word: str) -> int:
    """Count whole-word occurrences of `word` in `text`."""
    return (
        text.count(f" {word} ")
        + (1 if text.startswith(word + " ") else 0)
        + (1 if text.endswith(" " + word) else 0)
        + (1 if text == word else 0)
    )
