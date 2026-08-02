"""Tests for the download backlog."""

from doubles import make_ref

from maxicrawler.downloader import DownloadJob, DownloadQueue


def job(
    resource_id: str = "AaBbCcDd", *, priority: int = 0, parent: str | None = None
) -> DownloadJob:
    """Return a job addressing one resource."""
    return DownloadJob(ref=make_ref(resource_id, parent_id=parent), priority=priority)


def test_an_empty_queue_hands_out_nothing() -> None:
    queue = DownloadQueue()

    assert queue.pop() is None
    assert len(queue) == 0
    assert bool(queue) is False


def test_jobs_come_out_in_the_order_they_went_in() -> None:
    queue = DownloadQueue([job("Aaaaaaaa"), job("Bbbbbbbb"), job("Cccccccc")])

    assert [popped.ref.resource_id for popped in queue.drain()] == [
        "Aaaaaaaa",
        "Bbbbbbbb",
        "Cccccccc",
    ]


def test_a_higher_priority_overtakes() -> None:
    queue = DownloadQueue([job("Aaaaaaaa"), job("Bbbbbbbb", priority=10), job("Cccccccc")])

    assert [popped.ref.resource_id for popped in queue.drain()] == [
        "Bbbbbbbb",
        "Aaaaaaaa",
        "Cccccccc",
    ]


def test_equal_priorities_keep_their_order() -> None:
    queue = DownloadQueue(job(f"Handle{index:02d}", priority=5) for index in range(20))

    order = [popped.ref.resource_id for popped in queue.drain()]

    assert order == sorted(order)


def test_the_same_resource_is_queued_once() -> None:
    queue = DownloadQueue()

    assert queue.push(job()) is True
    assert queue.push(job()) is False
    assert len(queue) == 1


def test_a_resource_reached_through_two_links_is_queued_once() -> None:
    queue = DownloadQueue()
    with_key = DownloadJob(ref=make_ref(secret="0123456789abcdefghijkl"))
    without_key = DownloadJob(ref=make_ref())

    queue.push(with_key)

    assert queue.push(without_key) is False


def test_the_same_identifier_in_two_containers_is_two_jobs() -> None:
    queue = DownloadQueue()

    queue.push(job("FileAAA1", parent="FolderAA"))

    assert queue.push(job("FileAAA1", parent="FolderBB")) is True


def test_a_resource_is_not_requeued_after_it_was_handed_out() -> None:
    queue = DownloadQueue([job()])
    queue.pop()

    assert queue.push(job()) is False


def test_extend_reports_how_many_jobs_were_new() -> None:
    queue = DownloadQueue()

    assert queue.extend([job("Aaaaaaaa"), job("Bbbbbbbb"), job("Aaaaaaaa")]) == 2


def test_membership_is_by_resource_identity() -> None:
    queue = DownloadQueue([job()])

    assert job() in queue
    assert job("Zzzzzzzz") not in queue
    assert "AaBbCcDd" not in queue


def test_draining_sees_jobs_pushed_while_it_runs() -> None:
    queue = DownloadQueue([job("Aaaaaaaa")])
    seen = []

    for popped in queue.drain():
        seen.append(popped.ref.resource_id)
        if popped.ref.resource_id == "Aaaaaaaa":
            queue.push(job("Bbbbbbbb"))

    assert seen == ["Aaaaaaaa", "Bbbbbbbb"]


def test_the_representation_names_the_backlog_size() -> None:
    assert repr(DownloadQueue([job()])) == "DownloadQueue(pending=1)"
