import pytest

from instapostsforge.captions import CaptionRules, normalize_caption
from instapostsforge.captions.validator import CaptionError
from instapostsforge.models import Caption


def caption(**kw):
    base = dict(text="Golden hour over the lake.", hashtags=["#lake", "#goldenhour", "#landscape"], alt_text="A lake.")
    base.update(kw)
    return Caption(**base)


def test_hashtags_are_cleaned_deduped_and_capped():
    raw = caption(hashtags=["lake", "#Lake", "#golden hour", "#viral", "#a", "#b", "#c", "#d"])
    result = normalize_caption(raw, CaptionRules())
    assert result.hashtags == ["#lake", "#goldenhour", "#a", "#b", "#c"]


def test_too_few_hashtags_is_rejected():
    with pytest.raises(CaptionError):
        normalize_caption(caption(hashtags=["#explore", "#lake"]), CaptionRules())


def test_hashtags_inside_text_are_removed():
    result = normalize_caption(caption(text="Calm morning #nofilter by the water"), CaptionRules())
    assert "#" not in result.text


def test_render_includes_quote_and_tags():
    rendered = caption(quote="Stillness speaks.", quote_author=None).render()
    assert rendered.splitlines()[0] == "Golden hour over the lake."
    assert "“Stillness speaks.”" in rendered
    assert rendered.endswith("#lake #goldenhour #landscape")


def test_over_length_caption_is_rejected():
    with pytest.raises(CaptionError):
        normalize_caption(caption(text="x" * 2300), CaptionRules())


def test_short_caption_is_left_alone():
    from instapostsforge.captions import trim_caption_text

    assert trim_caption_text("  Rain on the glass. Who else loves this?  ", 150) == "Rain on the glass. Who else loves this?"


def test_trim_drops_body_sentences_and_keeps_hook_and_question():
    from instapostsforge.captions import trim_caption_text

    text = "Rain on the glass. The marigold kept its colour all evening. Behind it, the balcony went grey. Who else watches the rain?"
    trimmed = trim_caption_text(text, 90)
    assert trimmed == "Rain on the glass. The marigold kept its colour all evening. Who else watches the rain?"
    assert trim_caption_text(text, 60) == "Rain on the glass. Who else watches the rain?"


def test_trim_keeps_line_breaks_and_drops_the_question_last():
    from instapostsforge.captions import trim_caption_text

    text = "Rain on the glass.\n\nThe marigold kept its colour all evening. Who else watches the rain?"
    assert trim_caption_text(text, 30) == "Rain on the glass."
    assert trim_caption_text("x" * 200, 150) is None
