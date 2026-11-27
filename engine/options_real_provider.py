"""Read-only market-data acquisition for the limited research pilot."""
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import io
from pathlib import Path
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import zipfile
import csv

PILOT = ('SPY', 'QQQ', 'AAPL', 'NVDA', 'MSFT')
SOURCE = 'Cboe DataShop Option EOD Summary sample 217'
URL = 'https://datashop.cboe.com/download/sample/217'
MEMBER = 'UnderlyingOptionsEODCalcs_2023-08-25_cgi_or_historical.csv'
MAX_ZIP = 8_000_000
MAX_CSV = 20_000_000
COLUMNS = set('underlying_symbol quote_date root expiration strike option_type close trade_volume bid_1545 ask_1545 active_underlying_price_1545 implied_volatility_1545 open_interest delivery_code'.split())


@dataclass(frozen=True)
class Response:
    status: str
    received_at: datetime | None = None
    body: bytes | None = None
    reason: str = ''


class Provider(Protocol):
    def fetch(self) -> Response: ...


class CboeSample:
    """One fixed historical sample, optionally imported from a local copy."""

    def __init__(self, path: Path | None = None, *, opener=urlopen, sleep=time.sleep):
        self.path, self.opener, self.sleep = path, opener, sleep

    def fetch(self):
        if self.path is not None:
            try:
                with self.path.open('rb') as stream:
                    body = stream.read(MAX_ZIP + 1)
                return checked_body(body)
            except OSError:
                return Response('UNAVAILABLE', reason='local_sample_unreadable')
        for attempt in range(3):
            delay = 2**attempt
            try:
                with self.opener(URL, timeout=12) as stream:
                    return checked_body(stream.read(MAX_ZIP + 1))
            except HTTPError as exc:
                retry = exc.headers.get('Retry-After') if exc.headers else None
                status = exc.code
                exc.close()
                if status not in (429, 500, 502, 503, 504):
                    return Response('UNAVAILABLE', reason=f'http_{status}')
                if retry:
                    try:
                        delay = int(retry)
                    except ValueError:
                        try:
                            delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError):
                            return Response('UNAVAILABLE', reason=f'http_{status}_invalid_retry_after')
                if delay > 8 or attempt == 2:
                    return Response('UNAVAILABLE', reason=f'http_{status}')
            except (URLError, OSError, http.client.HTTPException):
                if attempt == 2:
                    return Response('UNAVAILABLE', reason='network_error')
            self.sleep(max(0, delay))
        raise AssertionError('Retry loop exhausted')


def checked_body(body):
    now = datetime.now(timezone.utc)
    if not body:
        return Response('UNAVAILABLE', now, reason='empty_response')
    if len(body) > MAX_ZIP:
        return Response('INVALID', now, reason='response_too_large')
    return Response('RECEIVED', now, body)


def sample_rows(body):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = [member for member in archive.infolist() if member.filename == MEMBER]
        if len(members) != 1 or members[0].file_size > MAX_CSV:
            raise ValueError('unexpected_sample_layout')
        with io.TextIOWrapper(archive.open(members[0]), encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, strict=True)
            if not reader.fieldnames or not COLUMNS.issubset(reader.fieldnames):
                raise ValueError('missing_source_columns')
            for row in reader:
                if None in row or any(row[k] is None for k in COLUMNS):
                    raise ValueError('malformed_source_row')
                if row['underlying_symbol'] in PILOT:
                    yield row
