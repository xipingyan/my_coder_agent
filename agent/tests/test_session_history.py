import json
import time

from coder_agent.session import Session


def make(project, prompts, finish=True):
    s = Session(project)
    for p in prompts:
        t = s.new_turn(p)
        t.messages.append({"role": "assistant", "content": "ok"})
        if finish:
            s.finish_turn(t, "done", "ok")
    return s


def test_summaries_list_newest_first_with_title_and_counts(project):
    a = make(project, ["first line of A\nsecond line", "A again"])
    time.sleep(0.02)
    b = make(project, ["B task"])
    infos = Session.summaries(project)
    assert [i.id for i in infos] == [b.id, a.id]
    assert infos[1].title == "first line of A" and infos[1].turns == 2 and infos[1].status == "done"


def test_empty_sessions_are_hidden_and_rewound_to_empty_too(project):
    s = Session(project)
    s.save()
    assert Session.summaries(project) == []
    t = s.new_turn("x")
    s.finish_turn(t, "done")
    assert len(Session.summaries(project)) == 1
    s.rewind(t.id)
    assert Session.summaries(project) == []


def test_running_turn_is_saved_immediately_and_reloaded_as_interrupted(project):
    s = Session(project)
    s.new_turn("long task")
    s.save()  # what the engine does when a turn starts
    assert Session.summaries(project)[0].status == "running"
    assert Session.load(project, s.id).path()[0].status == "interrupted"


def test_meta_is_backfilled_for_sessions_saved_by_older_versions(project):
    s = make(project, ["legacy"])
    s.file.with_suffix(".meta").unlink()
    infos = Session.summaries(project)
    assert infos[0].title == "legacy" and s.file.with_suffix(".meta").exists()


def test_corrupt_session_file_is_skipped(project):
    s = make(project, ["good"])
    (s.file.parent / "20200101-000000-deadbeef.json").write_text("{not json")
    assert [i.id for i in Session.summaries(project)] == [s.id]


def test_delete_removes_files_and_snapshots(project):
    s = make(project, ["x"])
    (project / "f.txt").write_text("1")
    s.snapshots.begin(s.turns[s.head].id)
    s.snapshots.record(project / "f.txt")
    snap_dir = s.snapshots.dir
    assert snap_dir.exists()
    Session.delete(project, s.id)
    assert not s.file.exists() and not s.file.with_suffix(".meta").exists() and not snap_dir.exists()
    assert Session.summaries(project) == []
    assert (project / "f.txt").read_text() == "1"  # project files untouched
