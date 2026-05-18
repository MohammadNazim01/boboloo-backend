FILLER_WORDS = {
    "um", "uh", "hmm", "uhh", "umm",
    "like", "so", "well",
    "and then", "then", "and",
    "because", "but", "so then",
    "okay", "ok", "yeah", "no",
    "wait", "wait wait", "hold on",
    "look", "see", "listen",
    "guess what", "you know", "you know what",
    "i think", "i guess",
    "maybe", "probably",
    "just", "kinda", "sorta",
    "really", "very", "super",
    "actually", "basically",
    "oops", "uh oh",
    "huh", "what", "why",
    "like this", "like that",
    "something", "anything", "everything",
    "whatever", "stuff", "things",
    "and stuff", "and things",
    "again", "one more", "one more time",
    "this one", "that one",
    "here", "there",
    "i don't know", "i dunno",
    "let me see", "lemme see",
    "wait a second", "just wait",
}


def process_vocabulary(docs, all_unique_words) -> dict:
    """
    Extracts vocabulary metrics from pre-computed spaCy docs.

    Accepts the existing-vocabulary list/set from the DB and returns new words,
    updated vocabulary, and token counts for downstream CMI / storage use.
    """
    total_words = 0
    session_unique: set[str] = set()
    all_unique_words_set = set(all_unique_words)

    for doc in docs:
        for token in doc:
            if not token.is_alpha:
                continue
            total_words += 1
            if token.is_stop:
                continue
            word = token.text.lower()
            if word in FILLER_WORDS:
                continue
            session_unique.add(token.lemma_.lower())

    new_words = session_unique - all_unique_words_set
    all_unique_words_set.update(new_words)

    return {
        "TotalWordsCount": total_words,
        "UniqueWordsList": session_unique,
        "UniqueWordsCount": len(session_unique),
        "NewWordsList": new_words,
        "NewWordsCount": len(new_words),
        "UpdatedVocabulary": all_unique_words_set,
    }
