from app.text import normalize_whitespace


def word_lengths(text):
    return [len(word) for word in normalize_whitespace(text).split(" ") if word]
