"""What the right-click menu says.

Building and showing the menu need a real Tk root, and `tests/unit/test_overlay.py`
covers those. What is checked here is the wording — which entries carry a tick and how
the fold entry flips — because those are the strings the user actually reads, and they
can be pinned without a screen.
"""

from voice_typer import card_menu


def test_an_entry_that_is_on_carries_a_tick():
    assert card_menu._ticked(card_menu.LISTENING, True) == "✓ F9-ის მოსმენა"


def test_an_entry_that_is_off_carries_no_tick():
    assert card_menu._ticked(card_menu.LISTENING, False) == "F9-ის მოსმენა"


def test_the_tick_is_a_prefix_rather_than_a_different_label():
    """The label must stay recognisable when it is ticked; a second wording for the same
    entry is how a menu starts contradicting itself."""
    for label in (card_menu.LISTENING, card_menu.REWRITE_MODE):
        assert card_menu._ticked(label, True).endswith(label)


def test_the_fold_entry_offers_the_opposite_of_the_current_shape():
    """It names the action, not the state — a folded card offers "unfold"."""
    assert card_menu.FOLD != card_menu.UNFOLD
