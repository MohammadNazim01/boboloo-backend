import nltk
from nltk.sentiment.vader import SentimentIntensityAnalyzer

try:
    nltk.data.find("sentiment/vader_lexicon.zip")
except LookupError:
    nltk.download("vader_lexicon", quiet=True)

_sia = SentimentIntensityAnalyzer()


def analyze_sentiment(text_list: list[str]) -> dict:
    if not text_list:
        return {"compound": 0.0, "positive": 0.0, "negative": 0.0, "neutral": 1.0}

    totals = {"compound": 0.0, "pos": 0.0, "neg": 0.0, "neu": 0.0}
    for text in text_list:
        scores = _sia.polarity_scores(text)
        totals["compound"] += scores["compound"]
        totals["pos"] += scores["pos"]
        totals["neg"] += scores["neg"]
        totals["neu"] += scores["neu"]

    n = len(text_list)
    return {
        "compound": round(totals["compound"] / n, 4),
        "positive": round(totals["pos"] / n, 4),
        "negative": round(totals["neg"] / n, 4),
        "neutral": round(totals["neu"] / n, 4),
    }
