from app.classifier import classify_page, recommended_age_policy_name
from app.extraction import ExtractedPage


def test_weighted_rules_return_evidence_and_confidence() -> None:
    scores = classify_page(
        ExtractedPage(
            title="Online University Courses",
            description="Learn with guided lessons",
            visible_text="Course tutorial lesson school education",
        )
    )
    assert scores[0].slug == "education"
    assert scores[0].confidence > 50
    assert scores[0].evidence


def test_age_policy_calculation_prioritizes_restricted_content() -> None:
    adult = ExtractedPage("Casino", "Online betting", "Gambling games")
    social = ExtractedPage("Community", "Chat with friends", "Social profiles")
    safe = ExtractedPage("Math", "Lessons", "Learn arithmetic")
    assert recommended_age_policy_name(adult, "entertainment") == "Adult"
    assert recommended_age_policy_name(social, "social") == "Teen"
    assert recommended_age_policy_name(safe, "education") == "Children"
