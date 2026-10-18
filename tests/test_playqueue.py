from noticom_node.playqueue import MAX_WAITING, Job, PlayQueue


def chime(n):
    return Job(job_id=f"c{n}", kind="chime", chime_hash="h")


def clip(n):
    return Job(job_id=f"m{n}", kind="clip", clip_url="/x")


def ids(q):
    return [j.job_id for j in q.waiting]


def test_first_job_plays_immediately():
    q = PlayQueue()
    assert q.add(clip(1))
    assert q.current.job_id == "m1"
    assert q.waiting == []


def test_sixth_waiting_job_is_dropped():
    q = PlayQueue()
    q.add(clip(0))  # playing
    for n in range(1, MAX_WAITING + 1):
        assert q.add(clip(n))
    assert not q.add(clip(99))
    assert ids(q) == ["m1", "m2", "m3", "m4", "m5"]


def test_chime_priority_jumps_clips_but_not_other_chimes():
    q = PlayQueue(chime_priority=True)
    q.add(clip(0))  # playing, never interrupted
    q.add(clip(1))
    q.add(chime(1))
    q.add(clip(2))
    q.add(chime(2))
    assert q.current.job_id == "m0"
    assert ids(q) == ["c1", "c2", "m1", "m2"]


def test_priority_off_is_fifo():
    q = PlayQueue(chime_priority=False)
    q.add(clip(0))
    q.add(clip(1))
    q.add(chime(1))
    assert ids(q) == ["m1", "c1"]


def test_priority_chime_still_dropped_when_full():
    q = PlayQueue()
    q.add(clip(0))
    for n in range(1, MAX_WAITING + 1):
        q.add(clip(n))
    assert not q.add(chime(1))


def test_finish_and_start_next():
    q = PlayQueue()
    q.add(clip(0))
    q.add(clip(1))
    assert q.finish_current().job_id == "m0"
    assert q.start_next().job_id == "m1"
    assert q.depth == 1


def test_clear_returns_everything():
    q = PlayQueue()
    q.add(clip(0))
    q.add(chime(1))
    assert [j.job_id for j in q.clear()] == ["m0", "c1"]
    assert q.depth == 0


def test_from_message_keeps_unknown_fields():
    job = Job.from_message(
        {"t": "play", "job_id": "j", "kind": "clip", "clip_url": "/c", "priority": 1}
    )
    assert job.clip_url == "/c"
    assert job.extra == {"priority": 1}
