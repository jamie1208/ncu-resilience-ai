"""P8-T07：通知失敗重試（v1.0 A10）。

- 第一次送出失敗記為 failed，排定下次時間；之後由排程最多重試 3 次，間隔遞增；
- 第 3 次重試仍失敗就停止（不再排定）；成功後不再重送；
- 唯一約束仍防重複遞送：重試沿用同一列，不新增列；兩個 worker 同時重試只送一次；
- 通知已撤回、過期或已改版時，舊版本不再重試。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.alerts.delivery import (
    DELIVERED,
    FAILED,
    MAX_RETRIES,
    OWNER,
    RETRY_DELAYS,
    RETRY_FAILED_DELIVERIES_TASK,
    deliver,
    retry_failed_deliveries,
)
from app.alerts.service import idempotency_key
from app.domain.states import AlertState, EventStatus
from app.models import Alert, AlertDelivery, Event, Location
from app.worker.celery_app import celery_app

pytestmark = pytest.mark.anyio

T0 = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)


class ScriptedChannel:
    """依序失敗 `failures` 次後成功的管道；`failures=None` 表示永遠失敗。"""

    def __init__(self, failures: int | None, name: str = "flaky") -> None:
        self.name = name
        self.failures = failures
        self.calls = 0

    def destination(self, audience: str) -> str:
        return f"{audience}@{self.name}"

    async def send(self, alert: Alert, destination: str) -> None:
        self.calls += 1
        if self.failures is None or self.calls <= self.failures:
            raise ConnectionError("simulated")


class SlowChannel(ScriptedChannel):
    """送出時稍等，讓另一個 worker 有機會同時重試。"""

    async def send(self, alert: Alert, destination: str) -> None:
        await asyncio.sleep(0.2)
        await super().send(alert, destination)


async def make_alert(session: AsyncSession, state: AlertState = AlertState.PUBLISHED) -> Alert:
    location = Location(name="工程一館")
    session.add(location)
    await session.flush()
    event = Event(
        title="工程一館停電",
        type="facility",
        status=EventStatus.VERIFIED.value,
        location_id=location.id,
        occurrence_start_date=date(2026, 11, 11),
    )
    session.add(event)
    await session.flush()
    alert = Alert(
        event_id=event.id,
        audience=OWNER,
        state=state.value,
        version=1,
        body="工程一館停電，請留意。",
        idempotency_key=idempotency_key(event.id, 1, OWNER),
    )
    session.add(alert)
    await session.commit()
    return alert


async def only_delivery(session: AsyncSession) -> AlertDelivery:
    session.expire_all()
    (row,) = (await session.scalars(select(AlertDelivery))).all()
    return row


async def fail_first(session: AsyncSession, channel: ScriptedChannel) -> Alert:
    alert = await make_alert(session)
    assert await deliver(session, alert, (channel,), now=T0) == ()
    await session.commit()
    return alert


# ---------- 設定 ----------


def test_retry_policy_is_three_retries_with_increasing_delays() -> None:
    assert MAX_RETRIES == 3
    assert len(RETRY_DELAYS) == MAX_RETRIES
    assert all(a < b for a, b in pairwise(RETRY_DELAYS))


def test_beat_schedules_retry_every_minute() -> None:
    entries = [
        e
        for e in celery_app.conf.beat_schedule.values()
        if e["task"] == RETRY_FAILED_DELIVERIES_TASK
    ]
    assert len(entries) == 1
    assert entries[0]["schedule"] == 60.0
    assert RETRY_FAILED_DELIVERIES_TASK in celery_app.tasks


# ---------- 第一次遞送 ----------


async def test_success_records_one_attempt_without_next_time(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=0)
    alert = await make_alert(session)

    assert await deliver(session, alert, (channel,), now=T0) == ("flaky",)
    await session.commit()

    row = await only_delivery(session)
    assert (row.status, row.attempts, row.next_attempt_at) == (DELIVERED, 1, None)


async def test_failure_is_recorded_and_retry_scheduled(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=None)
    await fail_first(session, channel)

    row = await only_delivery(session)
    assert (row.status, row.attempts) == (FAILED, 1)
    assert row.next_attempt_at == T0 + RETRY_DELAYS[0]
    assert channel.calls == 1


async def test_retry_waits_until_next_attempt_time(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=None)
    await fail_first(session, channel)

    await retry_failed_deliveries(
        session, (channel,), now=T0 + RETRY_DELAYS[0] - timedelta(seconds=1)
    )

    assert channel.calls == 1
    assert (await only_delivery(session)).attempts == 1


# ---------- 重試 ----------


async def test_stops_after_third_retry_still_fails(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=None)
    await fail_first(session, channel)

    now = T0
    expected_next = T0 + RETRY_DELAYS[0]
    for retry in range(1, MAX_RETRIES + 1):
        row = await only_delivery(session)
        assert row.next_attempt_at == expected_next
        now = expected_next
        result = await retry_failed_deliveries(session, (channel,), now=now)
        assert (result.retried, result.delivered) == (1, 0)
        row = await only_delivery(session)
        assert (row.status, row.attempts) == (FAILED, retry + 1)
        if retry < MAX_RETRIES:
            expected_next = now + RETRY_DELAYS[retry]
            assert row.next_attempt_at == expected_next

    row = await only_delivery(session)
    assert row.next_attempt_at is None
    assert channel.calls == 1 + MAX_RETRIES

    # 停止後再怎麼排程都不再送
    for later in (timedelta(hours=1), timedelta(days=7)):
        result = await retry_failed_deliveries(session, (channel,), now=now + later)
        assert result.retried == 0
    assert channel.calls == 1 + MAX_RETRIES
    assert (await only_delivery(session)).status == FAILED


async def test_success_on_retry_is_not_resent(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=2)
    alert = await fail_first(session, channel)

    first = await retry_failed_deliveries(session, (channel,), now=T0 + RETRY_DELAYS[0])
    assert (first.retried, first.delivered) == (1, 0)
    second_at = T0 + RETRY_DELAYS[0] + RETRY_DELAYS[1]
    second = await retry_failed_deliveries(session, (channel,), now=second_at)
    assert (second.retried, second.delivered) == (1, 1)

    row = await only_delivery(session)
    assert (row.status, row.attempts, row.next_attempt_at) == (DELIVERED, 3, None)
    assert channel.calls == 3

    for later in (timedelta(minutes=30), timedelta(days=1)):
        assert (
            await retry_failed_deliveries(session, (channel,), now=second_at + later)
        ).retried == 0
    # 同版本再遞送也只會被唯一約束擋下
    await session.refresh(alert)
    assert await deliver(session, alert, (channel,), now=second_at) == ()
    await session.commit()
    assert channel.calls == 3
    assert (await only_delivery(session)).status == DELIVERED


# ---------- 不重複遞送 ----------


async def test_redeliver_same_version_after_failure_does_not_add_row(
    session: AsyncSession,
) -> None:
    channel = ScriptedChannel(failures=None)
    alert = await fail_first(session, channel)

    assert await deliver(session, alert, (channel,), now=T0 + timedelta(seconds=5)) == ()
    await session.commit()

    assert channel.calls == 1
    assert (await only_delivery(session)).attempts == 1


@pytest.fixture()
async def second_session(test_db_url: URL, session: AsyncSession) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_db_url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def test_concurrent_retries_send_once(
    session: AsyncSession, second_session: AsyncSession
) -> None:
    channel = SlowChannel(failures=1)
    await fail_first(session, channel)

    due = T0 + RETRY_DELAYS[0]
    results = await asyncio.gather(
        retry_failed_deliveries(session, (channel,), now=due),
        retry_failed_deliveries(second_session, (channel,), now=due),
    )

    assert sorted(r.retried for r in results) == [0, 1]
    assert channel.calls == 2
    row = await only_delivery(session)
    assert (row.status, row.attempts) == (DELIVERED, 2)


# ---------- 停止重試 ----------


@pytest.mark.parametrize("state", [AlertState.RETRACTED, AlertState.EXPIRED])
async def test_inactive_alert_is_not_retried(session: AsyncSession, state: AlertState) -> None:
    channel = ScriptedChannel(failures=None)
    alert = await fail_first(session, channel)
    alert.state = state.value
    await session.commit()

    result = await retry_failed_deliveries(session, (channel,), now=T0 + RETRY_DELAYS[0])

    assert result.retried == 0
    assert channel.calls == 1
    row = await only_delivery(session)
    assert (row.status, row.next_attempt_at) == (FAILED, None)


async def test_superseded_version_is_not_retried(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=None)
    alert = await fail_first(session, channel)
    alert.version = 2
    alert.state = AlertState.UPDATED.value
    alert.idempotency_key = idempotency_key(alert.event_id, 2, OWNER)
    await session.commit()

    result = await retry_failed_deliveries(session, (channel,), now=T0 + RETRY_DELAYS[0])

    assert result.retried == 0
    assert channel.calls == 1
    assert (await only_delivery(session)).next_attempt_at is None


async def test_unknown_channel_is_left_for_its_worker(session: AsyncSession) -> None:
    channel = ScriptedChannel(failures=None)
    await fail_first(session, channel)
    other = ScriptedChannel(failures=0, name="other")

    result = await retry_failed_deliveries(session, (other,), now=T0 + RETRY_DELAYS[0])

    assert result.retried == 0
    assert other.calls == 0
    row = await only_delivery(session)
    assert row.next_attempt_at == T0 + RETRY_DELAYS[0]


async def test_retry_without_failed_deliveries_is_noop(session: AsyncSession) -> None:
    assert (await retry_failed_deliveries(session, now=T0)).retried == 0
