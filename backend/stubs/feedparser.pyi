# feedparser 6.0.x 沒有附型別資訊（無 py.typed、PyPI 也沒有 types-feedparser）。
# 只宣告本專案用到的 API；項目內容來自外部 feed，型別一律視為 object，使用前自行檢查。
#
# parse() 刻意只接受 bytes：傳入 str 時 feedparser 會把它當網址自行連線，
# 繞過我們的 HTTP 用戶端（timeout、User-Agent、大小上限）。

from collections.abc import Mapping

class FeedParserDict(dict[str, object]):
    bozo: bool | int
    entries: list[FeedParserDict]
    version: str

def parse(
    url_file_stream_or_string: bytes,
    *,
    response_headers: Mapping[str, str] | None = ...,
    resolve_relative_uris: bool | None = ...,
    sanitize_html: bool | None = ...,
) -> FeedParserDict: ...
