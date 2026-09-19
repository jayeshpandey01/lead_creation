SPAM_PHRASES = [
    "free money", "act now", "click here", "guarantee", "no obligation",
    "risk-free", "risk free", "winner", "congratulations", "limited time",
    "buy now", "cash bonus", "double your", "earn extra cash", "urgent",
    "100% free", "dear sir", "dear madam", "to whom it may concern",
]


def spam_score_issues(subject: str, body: str) -> list[str]:
    """Cheap heuristic gate before a draft is allowed to be sent. Not a
    substitute for real deliverability testing (Postmaster Tools etc.),
    just catches the obvious ways an LLM draft reads as spam or as a
    templated blast."""
    issues = []
    lowered = f"{subject}\n{body}".lower()

    for phrase in SPAM_PHRASES:
        if phrase in lowered:
            issues.append(f"contains spam-trigger phrase: '{phrase}'")

    word_count = len(body.split())
    if word_count > 150:
        issues.append(f"body too long ({word_count} words, target <=120)")
    if word_count < 20:
        issues.append(f"body too short ({word_count} words)")

    if body.count("http") > 1:
        issues.append("more than one link in body")

    if subject.isupper():
        issues.append("subject is all caps")

    if "!" in subject:
        issues.append("exclamation mark in subject")

    return issues
