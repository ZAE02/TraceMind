import pytest

from tracemind.experiments.interactions import validate_scores


@pytest.mark.parametrize(
    "achieved,quote,accepted",
    [
        (False, "", True),
        (True, "", False),
        (False, "invented", False),
        (False, " ", False),
        (True, "I fear blame", True),
        (True, "Are you afraid", False),
    ],
)
def test_absence_needs_no_quote_but_achievement_needs_real_client_quote(achieved, quote, accepted):
    scores = [{"id": "target", "achieved": achieved, "evidence": quote}]
    transcript = [
        {"role": "counselor", "text": "Are you afraid of blame?"},
        {"role": "client", "text": "I fear blame, yes."},
    ]
    if accepted:
        validate_scores(scores, ["target"], transcript, require_client=True)
    else:
        with pytest.raises(ValueError):
            validate_scores(scores, ["target"], transcript, require_client=True)
