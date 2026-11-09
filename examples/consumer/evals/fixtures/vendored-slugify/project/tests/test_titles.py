from app.titles import post_slug


def test_a_plain_title_becomes_a_slug():
    assert post_slug("Hello, World") == "hello-world"


def test_accented_letters_keep_their_base_letter():
    assert post_slug("Café Crème") == "cafe-creme"
