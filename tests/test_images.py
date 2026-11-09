import urllib.error

from images import download_missing, image_url, local_path


def test_image_url():
    entry = {"images": {"header": "https://x/h.jpg"}}
    assert image_url(entry) == "https://x/h.jpg"
    assert image_url(entry, "portrait") is None and image_url({}) is None


def test_download_missing_only_fetches_what_is_not_cached(tmp_path):
    games = {"1": {"images": {"header": "u1h", "portrait": "u1p"}}, "2": {"images": {"header": "u2h"}},
             "3": {}}
    local_path("1", "header", tmp_path).parent.mkdir(parents=True)
    local_path("1", "header", tmp_path).write_bytes(b"ya")
    calls = []

    def fetch(url):
        calls.append(url)
        if url == "u2h":
            raise urllib.error.URLError("404")
        return b"jpg"
    assert download_missing(games, ("header", "portrait"), tmp_path, fetch, sleep=lambda s: None) == (1, 1)
    assert calls == ["u1p", "u2h"]
    assert local_path("1", "portrait", tmp_path).read_bytes() == b"jpg"
    assert not local_path("2", "header", tmp_path).exists()


def test_download_limit(tmp_path):
    games = {str(i): {"images": {"header": f"u{i}"}} for i in range(5)}
    assert download_missing(games, ("header",), tmp_path, lambda u: b"x", limit=2,
                            sleep=lambda s: None) == (2, 0)
