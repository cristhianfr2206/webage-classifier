from app.extraction import extract_page


def test_metadata_and_visible_text_are_bounded_and_scripts_are_ignored() -> None:
    page = extract_page(
        """
        <html><head><title> Safe &amp; useful </title>
        <meta name="description" content="A learning site"></head>
        <body><h1>Course catalog</h1><script>stealCredentials()</script>
        <style>.secret { display:none }</style><p>Lessons for everyone.</p></body></html>
        """,
        text_limit=30,
    )
    assert page.title == "Safe & useful"
    assert page.description == "A learning site"
    assert "stealCredentials" not in page.visible_text
    assert ".secret" not in page.visible_text
    assert len(page.visible_text) <= 30
