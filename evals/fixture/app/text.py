import re


def normalize_whitespace(text):
    return re.sub(r"\s+", " ", text).strip()


def title_case(text):
    return " ".join(word.capitalize() for word in normalize_whitespace(text).split(" "))
